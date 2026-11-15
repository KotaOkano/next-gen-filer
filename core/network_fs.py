from __future__ import annotations
import os
import posixpath
import re
import socket
import stat
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

import paramiko

from core import file_ops
from core.known_hosts import STATUS_CHANGED, STATUS_KNOWN, STATUS_UNKNOWN, KnownHosts, fingerprint

CONNECT_TIMEOUT = 10.0  # 接続・バナー・認証のタイムアウト（秒）
OPERATION_TIMEOUT = 30.0  # 接続後の通信で、データが全く来ないと諦める時間（秒）
KEEPALIVE_SECONDS = 30

ProgressCallback = Callable[[int, int], None]  # (転送済みバイト数, 全体バイト数)
CancelCallback = Callable[[], bool]


# ----------------------------------------------------------------------
# 例外
# ----------------------------------------------------------------------
class SFTPError(Exception):
    """このモジュールの例外の基底クラス。メッセージはそのまま画面に出せる日本語。"""


class SFTPConnectionError(SFTPError):
    """接続できない（ホスト名・ポート・ネットワーク・タイムアウトなど）"""


class AuthenticationFailed(SFTPError):
    """認証に失敗した（ユーザー名・パスワード・鍵が違う、など）"""


class PassphraseRequired(SFTPError):
    """秘密鍵にパスフレーズが必要"""


class TransferCancelled(SFTPError):
    """ユーザーが転送を中断した"""


class HostKeyError(SFTPError):
    """サーバーの鍵が確認済みではないため、接続を中止した"""

    def __init__(self, info: "HostKeyInfo", message: str):
        super().__init__(message)
        self.info = info


class UnknownHostKeyError(HostKeyError):
    def __init__(self, info: "HostKeyInfo"):
        super().__init__(info, "このサーバーはまだ確認されていません。接続を中止しました。")


class ChangedHostKeyError(HostKeyError):
    def __init__(self, info: "HostKeyInfo"):
        super().__init__(
            info,
            "サーバーの鍵が、以前に確認したものと異なります。サーバーの入れ替え、または中間者攻撃の可能性があるため、"
            "接続を中止しました。",
        )


# ----------------------------------------------------------------------
# データクラス
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class HostKeyInfo:
    """サーバーが提示した鍵"""

    host: str
    port: int
    key_type: str  # 例: "ssh-ed25519"
    key_blob: bytes

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key_blob)


@dataclass
class RemoteEntry:
    name: str
    path: str
    size: int
    is_dir: bool
    mtime: Optional[float]
    permissions: str
    is_link: bool = False


# ----------------------------------------------------------------------
# パス・名前のヘルパー
# ----------------------------------------------------------------------
_INVALID_LOCAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


def safe_local_name(name: str) -> str:
    """リモートのファイル名を、ローカル（特に Windows）で安全に使える名前にする。

    Linux のサーバーには "..\\..\\x.txt" のようにバックスラッシュを含む正当なファイル名があり、
    そのまま Windows で使うとフォルダの外へ書き込まれてしまう。
    """
    cleaned = _INVALID_LOCAL_CHARS.sub("_", name).rstrip(" .")
    if not cleaned:
        return "download"
    if cleaned.split(".")[0].upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = "_" + cleaned
    return cleaned


def _make_callback(progress: Optional[ProgressCallback], is_cancelled: Optional[CancelCallback]):
    def callback(done: int, total: int) -> None:
        if is_cancelled is not None and is_cancelled():
            raise TransferCancelled("キャンセルされました")
        if progress is not None:
            progress(done, total)

    return callback


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ----------------------------------------------------------------------
# マネージャー
# ----------------------------------------------------------------------
class SFTPManager:
    """SFTP サーバーへの接続と、一覧の取得・ダウンロード・アップロードを行う"""

    def __init__(self, known_hosts_path: str):
        self.known_hosts = KnownHosts(known_hosts_path)
        self.transport = None
        self.sftp = None
        self.connected = False
        self.current_host = ""
        self.current_port = 0
        self.home_path = "/"
        self.current_path = "/"

    # ==================================================================
    # ホスト鍵の確認
    # ==================================================================
    def host_key_status(self, info: HostKeyInfo) -> str:
        return self.known_hosts.status(info.host, info.port, info.key_type, info.key_blob)

    def trust_host_key(self, info: HostKeyInfo) -> None:
        """ユーザーが確認した鍵を、信頼済みとして保存する"""
        self.known_hosts.add(info.host, info.port, info.key_type, info.key_blob)

    def forget_host(self, host: str, port: int) -> None:
        self.known_hosts.remove(host, port)

    def stored_fingerprints(self, host: str, port: int) -> list:
        """保存済みの鍵 [(鍵の種類, フィンガープリント), ...]"""
        return [(t, fingerprint(b)) for t, b in self.known_hosts.lookup(host, port)]

    def probe_host_key(self, hostname: str, port: int, timeout: float = CONNECT_TIMEOUT) -> HostKeyInfo:
        """認証はせずに、サーバーが提示する鍵だけを取得する（ユーザーに確認してもらうため）"""
        transport = None
        try:
            transport, key = self._open_transport(hostname, port, timeout)
            return HostKeyInfo(hostname, port, key.get_name(), key.asbytes())
        except SFTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate_error(exc, hostname, port) from exc
        finally:
            self._close_quietly(transport)

    def _prefer_known_key_types(self, transport, hostname: str, port: int) -> None:
        """保存済みの鍵の種類を優先して交渉する（サーバーが別の種類の鍵を出して「変更」と誤判定されるのを防ぐ）"""
        known = self.known_hosts.known_types(hostname, port)
        if not known:
            return
        options = transport.get_security_options()
        supported = list(options.key_types)
        ordered = [t for t in known if t in supported] + [t for t in supported if t not in known]
        options.key_types = tuple(ordered)

    def _open_transport(self, hostname: str, port: int, timeout: float):
        """TCP 接続して SSH のやり取り（鍵交換）まで進め、(transport, サーバーの鍵) を返す。認証はしない。"""
        sock = socket.create_connection((hostname, port), timeout=timeout)
        try:
            transport = paramiko.Transport(sock)
        except Exception:
            sock.close()
            raise

        try:
            transport.banner_timeout = timeout
            transport.handshake_timeout = timeout
            transport.auth_timeout = timeout
            self._prefer_known_key_types(transport, hostname, port)
            transport.start_client(timeout=timeout)
            key = transport.get_remote_server_key()
        except BaseException:
            self._close_quietly(transport)
            raise
        return transport, key

    # ==================================================================
    # 接続・切断
    # ==================================================================
    def connect(
        self,
        hostname: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        passphrase: Optional[str] = None,
        timeout: float = CONNECT_TIMEOUT,
    ) -> str:
        """SFTP サーバーへ接続し、ホームディレクトリのパスを返す。

        サーバーの鍵が確認済み（known_hosts に保存済みで一致）でなければ、認証せずに中止する。
        key_filename を指定すると鍵認証、指定しなければパスワード認証。
        """
        self.disconnect()

        pkey = self._load_private_key(key_filename, passphrase) if key_filename else None
        if pkey is None and not password:
            raise AuthenticationFailed("パスワードまたは秘密鍵ファイルを指定してください。")

        transport = None
        try:
            transport, key = self._open_transport(hostname, port, timeout)

            info = HostKeyInfo(hostname, port, key.get_name(), key.asbytes())
            status = self.host_key_status(info)
            if status == STATUS_UNKNOWN:
                raise UnknownHostKeyError(info)
            if status == STATUS_CHANGED:
                raise ChangedHostKeyError(info)

            self._authenticate(transport, username, password, pkey)

            sftp = paramiko.SFTPClient.from_transport(transport)
            if sftp is None:
                raise SFTPConnectionError(
                    "SFTP を開始できませんでした。サーバーで SFTP サブシステムが有効か確認してください。"
                )
            sftp.get_channel().settimeout(OPERATION_TIMEOUT)
            transport.set_keepalive(KEEPALIVE_SECONDS)
            home = sftp.normalize(".")
        except SFTPError:
            self._close_quietly(transport)
            raise
        except Exception as exc:  # noqa: BLE001
            self._close_quietly(transport)
            raise self._translate_error(exc, hostname, port) from exc

        self.transport, self.sftp = transport, sftp
        self.connected = True
        self.current_host, self.current_port = hostname, port
        self.home_path = self.current_path = home or "/"
        return self.home_path

    @staticmethod
    def _authenticate(transport, username: str, password: Optional[str], pkey) -> None:
        if pkey is not None:
            transport.auth_publickey(username, pkey)
            return
        try:
            transport.auth_password(username, password)
        except paramiko.BadAuthenticationType as exc:
            # NAS などパスワードを keyboard-interactive でしか受け付けないサーバー向け
            if "keyboard-interactive" not in (getattr(exc, "allowed_types", None) or []):
                raise
            transport.auth_interactive(username, lambda title, instructions, prompts: [password for _ in prompts])

    @staticmethod
    def _load_private_key(path: str, passphrase: Optional[str]):
        classes = [
            getattr(paramiko, name)
            for name in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey")
            if hasattr(paramiko, name)
        ]
        for key_class in classes:
            try:
                return key_class.from_private_key_file(path, password=passphrase or None)
            except paramiko.PasswordRequiredException:
                raise PassphraseRequired("この秘密鍵にはパスフレーズが必要です。パスフレーズを入力してください。") from None
            except paramiko.SSHException:
                continue  # 別の種類の鍵として試す
            except OSError as exc:
                raise SFTPError(f"秘密鍵ファイルを読み込めません: {exc}") from exc
        raise SFTPError(
            "秘密鍵を読み込めません。形式が未対応か、パスフレーズが違います。\n"
            "（OpenSSH 形式の RSA / Ed25519 / ECDSA に対応しています。PuTTY の .ppk 形式は変換が必要です）"
        )

    @staticmethod
    def _translate_error(exc: Exception, hostname: str, port: int) -> SFTPError:
        """paramiko や socket の例外を、画面に出せる日本語の例外にする"""
        where = f"{hostname}:{port}"
        if isinstance(exc, paramiko.PasswordRequiredException):
            return PassphraseRequired("この秘密鍵にはパスフレーズが必要です。パスフレーズを入力してください。")
        if isinstance(exc, paramiko.AuthenticationException):
            return AuthenticationFailed("認証に失敗しました。ユーザー名・パスワード（または秘密鍵）を確認してください。")
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return SFTPConnectionError(f"{where} への接続がタイムアウトしました。ホスト名・ポート・ネットワークを確認してください。")
        if isinstance(exc, socket.gaierror):
            return SFTPConnectionError(f"ホスト名を解決できません: {hostname}")
        if isinstance(exc, ConnectionRefusedError):
            return SFTPConnectionError(f"{where} に接続を拒否されました。ポート番号と、サーバーの SSH/SFTP が起動しているかを確認してください。")
        if isinstance(exc, (OSError, EOFError, paramiko.SSHException)):
            return SFTPConnectionError(f"{where} への接続に失敗しました: {exc}")
        return SFTPConnectionError(f"{where} への接続中にエラーが発生しました: {exc}")

    @staticmethod
    def _close_quietly(transport) -> None:
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass

    def disconnect(self) -> None:
        """接続を安全に切断する"""
        if self.sftp is not None:
            try:
                self.sftp.close()
            except Exception:  # noqa: BLE001
                pass
            self.sftp = None
        self._close_quietly(self.transport)
        self.transport = None
        self.connected = False
        self.current_host = ""
        self.current_port = 0
        self.home_path = self.current_path = "/"

    def _require_sftp(self):
        if self.sftp is None or not self.connected:
            raise SFTPError("SFTP サーバーに接続されていません。")
        return self.sftp

    # ==================================================================
    # 一覧
    # ==================================================================
    @staticmethod
    def parent_path(path: str) -> str:
        return posixpath.dirname(posixpath.normpath(path)) or "/"

    def _resolve(self, remote_path: Optional[str]) -> str:
        if not remote_path:
            return self.current_path
        if remote_path == "~":
            return self.home_path
        if remote_path.startswith("~/"):
            return posixpath.normpath(posixpath.join(self.home_path, remote_path[2:]))
        # 絶対パスならそのまま、相対パスなら現在のフォルダからの相対パスとして扱う
        return posixpath.normpath(posixpath.join(self.current_path, remote_path))

    def list_dir(self, remote_path: Optional[str] = None) -> list:
        """リモートのフォルダの一覧（フォルダが先、名前順）を返す。成功したときだけ現在のパスを更新する。"""
        sftp = self._require_sftp()
        path = self._resolve(remote_path)
        try:
            attrs = sftp.listdir_attr(path)
        except Exception as exc:  # noqa: BLE001
            raise SFTPError(f"フォルダを読み込めません ({path}): {exc}") from exc

        entries = [self._make_entry(sftp, path, a) for a in attrs if a.filename not in (".", "..")]
        entries.sort(key=lambda e: (not e.is_dir, e.name.casefold()))
        self.current_path = path
        return entries

    @staticmethod
    def _make_entry(sftp, directory: str, attr) -> RemoteEntry:
        mode = attr.st_mode or 0
        full_path = posixpath.join(directory, attr.filename)
        size = attr.st_size or 0
        mtime = attr.st_mtime
        is_dir = stat.S_ISDIR(mode)
        is_link = stat.S_ISLNK(mode)

        if is_link:  # リンク先がフォルダなら、フォルダとして扱う
            try:
                target = sftp.stat(full_path)
                target_mode = target.st_mode or 0
                is_dir = stat.S_ISDIR(target_mode)
                size = target.st_size or 0
                mtime = target.st_mtime or mtime
            except Exception:  # noqa: BLE001 - 壊れたリンク
                pass

        return RemoteEntry(
            name=attr.filename,
            path=full_path,
            size=0 if is_dir else size,
            is_dir=is_dir,
            mtime=mtime,
            permissions=stat.filemode(mode) if mode else "",
            is_link=is_link,
        )

    # ==================================================================
    # ダウンロード / アップロード
    # ==================================================================
    def download_file(
        self,
        remote_path: str,
        local_dir: str,
        progress: Optional[ProgressCallback] = None,
        is_cancelled: Optional[CancelCallback] = None,
    ) -> str:
        """リモートのファイルを local_dir にダウンロードし、保存先のパスを返す。

        一時ファイルに書いて、成功したときだけ本来の名前にする。同名のファイルがあっても上書きしない。
        """
        sftp = self._require_sftp()
        if not os.path.isdir(local_dir):
            raise SFTPError(f"保存先のフォルダが見つかりません: {local_dir}")

        remote_name = posixpath.basename(remote_path.rstrip("/"))
        if not remote_name or remote_name in (".", ".."):
            raise SFTPError(f"ファイル名を判別できません: {remote_path}")

        remote_attr = sftp.stat(remote_path)
        if stat.S_ISDIR(remote_attr.st_mode or 0):
            raise SFTPError("フォルダのダウンロードには未対応です。")
        expected = remote_attr.st_size or 0

        callback = _make_callback(progress, is_cancelled)
        callback(0, expected)  # 開始前にもキャンセルを確認する

        fd, tmp_path = tempfile.mkstemp(dir=local_dir, prefix=".ngf-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as out:
                sftp.getfo(remote_path, out, callback=callback)
                out.flush()
                os.fsync(out.fileno())
            actual = os.path.getsize(tmp_path)
            if actual != expected:
                raise SFTPError(f"ダウンロードしたサイズが一致しません（{actual} / {expected} バイト）。")
            final_path = file_ops.unique_path(os.path.join(local_dir, safe_local_name(remote_name)))
            os.replace(tmp_path, final_path)
            return final_path
        except BaseException:
            _remove_quietly(tmp_path)
            raise

    def _remote_exists(self, sftp, path: str) -> bool:
        try:
            sftp.lstat(path)
            return True
        except FileNotFoundError:
            return False

    def _unique_remote_path(self, sftp, path: str) -> str:
        """リモートで衝突しないパス（"name (2).ext" の形）"""
        if not self._remote_exists(sftp, path):
            return path
        root, ext = posixpath.splitext(path)
        n = 2
        while True:
            candidate = f"{root} ({n}){ext}"
            if not self._remote_exists(sftp, candidate):
                return candidate
            n += 1

    def upload_file(
        self,
        local_path: str,
        remote_dir: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
        is_cancelled: Optional[CancelCallback] = None,
    ) -> str:
        """ローカルのファイルを remote_dir（省略時は現在のフォルダ）へアップロードし、リモートのパスを返す。

        一時ファイル名で送り、成功したときだけ rename する。同名のファイルがあっても上書きしない。
        （通信が切れた場合は、一時ファイル ".名前.ngf-xxxx.part" がサーバーに残ることがある）
        """
        sftp = self._require_sftp()
        if not os.path.isfile(local_path):
            raise SFTPError(f"ファイルが見つかりません: {local_path}")

        directory = remote_dir or self.current_path
        name = os.path.basename(local_path)
        size = os.path.getsize(local_path)
        tmp_remote = posixpath.join(directory, f".{name}.ngf-{os.urandom(4).hex()}.part")

        callback = _make_callback(progress, is_cancelled)
        callback(0, size)  # 開始前にもキャンセルを確認する

        try:
            with open(local_path, "rb") as source:
                sftp.putfo(source, tmp_remote, file_size=size, callback=callback, confirm=True)
            final_remote = self._unique_remote_path(sftp, posixpath.join(directory, name))
            sftp.rename(tmp_remote, final_remote)  # SFTP の rename は、宛先が既にあるとエラーになる
            return final_remote
        except BaseException:
            try:
                sftp.remove(tmp_remote)
            except Exception:  # noqa: BLE001
                pass
            raise

from __future__ import annotations

import base64
import os
import stat
import struct
import tempfile
import unicodedata
from typing import Callable, Iterator, Optional

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core import file_ops

# ----------------------------------------------------------------------
# 定数
# ----------------------------------------------------------------------
ENC_EXT = ".enc"
MAGIC = b"NGFE"
VERSION = 1
HEADER_FMT = ">4sBII16s7s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 36
TAG_SIZE = 16

PBKDF2_ITERATIONS = 600_000 
MIN_ITERATIONS = 1_000
MAX_ITERATIONS = 5_000_000 
CHUNK_SIZE = 1024 * 1024  # 1 MiB
MIN_CHUNK_SIZE = 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024

LEGACY_ITERATIONS = 100_000  # 旧形式 (Fernet) の反復回数
LEGACY_SALT_SIZE = 16

SHRED_CHUNK_SIZE = 1024 * 1024
DEFAULT_SHRED_PASSES = 3

ProgressCallback = Callable[[int, int], None]  # (処理済みバイト数, 全体バイト数)
CancelCallback = Callable[[], bool]


# ----------------------------------------------------------------------
# 例外
# ----------------------------------------------------------------------
class FileToolError(Exception):
    """このモジュールの例外の基底クラス"""


class OperationCancelled(FileToolError):
    """ユーザーが操作を中断した"""


class InvalidFileError(FileToolError):
    """暗号化ファイルとして読めない（形式が違う・短すぎる・設定値が不正）"""


class DecryptionError(FileToolError):
    """復号できない（パスワードが違う、またはファイルが破損・改ざんされている）"""

    def __init__(self, message: str = "パスワードが違うか、ファイルが破損・改ざんされています。"):
        super().__init__(message)


# ----------------------------------------------------------------------
# 内部ヘルパー
# ----------------------------------------------------------------------
def _check_cancel(is_cancelled: Optional[CancelCallback]) -> None:
    if is_cancelled is not None and is_cancelled():
        raise OperationCancelled("キャンセルされました")


def _report(progress: Optional[ProgressCallback], done: int, total: int) -> None:
    if progress is not None:
        progress(done, total)


def _require_file(path: str) -> None:
    if os.path.islink(path) or not os.path.isfile(path):
        raise FileNotFoundError(f"ファイルが見つかりません（フォルダやリンクは対象外です）: {path}")


def _password_bytes(password: str) -> bytes:
    if not password:
        raise ValueError("パスワードが空です")
    return unicodedata.normalize("NFC", password).encode("utf-8")


def _derive_key(password: bytes, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password)


def _nonce(prefix: bytes, counter: int, final: bool) -> bytes:
    return prefix + counter.to_bytes(4, "big") + (b"\x01" if final else b"\x00")


def _unique_enc_path(path: str) -> str:
    if not os.path.lexists(path):
        return path
    base = path[: -len(ENC_EXT)] if path.endswith(ENC_EXT) else path
    root, ext = os.path.splitext(base)
    n = 2
    while True:
        candidate = f"{root} ({n}){ext}{ENC_EXT}"
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def _unique(path: str) -> str:
    return _unique_enc_path(path) if path.endswith(ENC_EXT) else file_ops.unique_path(path)


class _AtomicOutput:


    def __init__(self, desired: str, explicit: bool):
        self._desired = desired
        self._explicit = explicit
        directory = os.path.dirname(os.path.abspath(desired))
        fd, self._tmp_path = tempfile.mkstemp(dir=directory, prefix=".ngf-", suffix=".tmp")
        self.file = os.fdopen(fd, "wb")

    def commit(self) -> str:
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()

        if self._explicit:
            if os.path.lexists(self._desired):
                raise FileExistsError(f"出力先に同名のファイルがあります: {self._desired}")
            target = self._desired
        else:
            target = _unique(self._desired) 
        os.replace(self._tmp_path, target)
        return target

    def abort(self) -> None:
        try:
            self.file.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            os.remove(self._tmp_path)
        except OSError:
            pass


def _resolve_output(default_path: str, output_path: Optional[str]) -> tuple:
    """(出力先, 明示指定か) を返す。明示指定で既に存在する場合は、処理を始める前に失敗させる。"""
    if output_path:
        if os.path.lexists(output_path):
            raise FileExistsError(f"出力先に同名のファイルがあります: {output_path}")
        return output_path, True
    return default_path, False


# ----------------------------------------------------------------------
# 形式の判定
# ----------------------------------------------------------------------
def has_encrypted_header(file_path: str) -> bool:
    """このアプリの暗号化形式 (version 1) のヘッダーを持つファイルか"""
    try:
        with open(file_path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def looks_encrypted(file_path: str) -> bool:
    """暗号化済みに見えるか（専用ヘッダー、または .enc 拡張子）"""
    return file_path.lower().endswith(ENC_EXT) or has_encrypted_header(file_path)


# ----------------------------------------------------------------------
# 暗号化
# ----------------------------------------------------------------------
def encrypt_file(
    file_path: str,
    password: str,
    output_path: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
    iterations: int = PBKDF2_ITERATIONS,
) -> str:
    """file_path を暗号化して '<元のファイル名>.enc' に保存し、そのパスを返す。

    元のファイルには触れない。同名の出力があれば別名にする（output_path を明示した場合はエラー）。
    """
    _require_file(file_path)
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError("反復回数が範囲外です")
    pwd = _password_bytes(password)
    desired, explicit = _resolve_output(file_path + ENC_EXT, output_path)

    salt, prefix = os.urandom(16), os.urandom(7)
    header = struct.pack(HEADER_FMT, MAGIC, VERSION, iterations, CHUNK_SIZE, salt, prefix)
    aes = AESGCM(_derive_key(pwd, salt, iterations))
    total = os.path.getsize(file_path)

    out = _AtomicOutput(desired, explicit)
    try:
        with open(file_path, "rb") as src:
            out.file.write(header)
            counter, done = 0, 0
            chunk = src.read(CHUNK_SIZE)
            while True:
                _check_cancel(is_cancelled)
                upcoming = src.read(CHUNK_SIZE)  # 先読みして「最後のチャンクか」を判定する
                final = not upcoming
                out.file.write(aes.encrypt(_nonce(prefix, counter, final), chunk, header))
                done += len(chunk)
                _report(progress, done, total)
                if final:
                    break
                chunk = upcoming
                counter += 1
        return out.commit()
    except BaseException:
        out.abort()
        raise


# ----------------------------------------------------------------------
# 復号
# ----------------------------------------------------------------------
def _parse_header(header: bytes) -> tuple:
    if len(header) < HEADER_SIZE:
        raise InvalidFileError("暗号化ファイルとして読めません（ファイルが短すぎます）。")
    magic, version, iterations, chunk_size, salt, prefix = struct.unpack(HEADER_FMT, header)
    if magic != MAGIC:
        raise InvalidFileError("このアプリで暗号化したファイルではありません。")
    if version != VERSION:
        raise InvalidFileError(f"未対応の形式です（version {version}）。アプリを最新版にしてください。")
    if not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise InvalidFileError("暗号化ファイルのヘッダーが不正です（反復回数）。")
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise InvalidFileError("暗号化ファイルのヘッダーが不正です（チャンクサイズ）。")
    return iterations, chunk_size, salt, prefix


def _iter_plaintext_chunks(
    file_path: str,
    password: str,
    progress: Optional[ProgressCallback],
    is_cancelled: Optional[CancelCallback],
) -> Iterator[bytes]:
    """version 1 のファイルを 1 チャンクずつ復号して返す。パスワード違い・改ざんは DecryptionError。"""
    pwd = _password_bytes(password)
    size = os.path.getsize(file_path)

    with open(file_path, "rb") as src:
        header = src.read(HEADER_SIZE)
        iterations, chunk_size, salt, prefix = _parse_header(header)

        body = size - HEADER_SIZE
        block_size = chunk_size + TAG_SIZE
        if body < TAG_SIZE:
            raise InvalidFileError("暗号化ファイルとして読めません（データがありません）。")
        chunk_count = -(-body // block_size)  # 切り上げ
        if body - (chunk_count - 1) * block_size < TAG_SIZE:
            raise InvalidFileError("暗号化ファイルが壊れています（末尾が不完全です）。")

        aes = AESGCM(_derive_key(pwd, salt, iterations))
        consumed = 0
        for index in range(chunk_count):
            _check_cancel(is_cancelled)
            block = src.read(block_size)
            final = index == chunk_count - 1
            try:
                plain = aes.decrypt(_nonce(prefix, index, final), block, header)
            except InvalidTag:
                raise DecryptionError() from None
            consumed += len(block)
            _report(progress, consumed, body)
            yield plain


def _default_decrypt_path(file_path: str) -> str:
    if file_path.lower().endswith(ENC_EXT):
        return file_path[: -len(ENC_EXT)]
    return file_path + ".decrypted"


def _decrypt_legacy(file_path: str, password: str) -> bytes:
    """旧形式（salt(16) + Fernet トークン）を復号する。Fernet は全体をメモリに読み込む。"""
    with open(file_path, "rb") as f:
        content = f.read()
    if len(content) <= LEGACY_SALT_SIZE:
        raise InvalidFileError("暗号化ファイルとして読めません（ファイルが短すぎます）。")

    salt, token = content[:LEGACY_SALT_SIZE], content[LEGACY_SALT_SIZE:]

    # 旧版は NFC 正規化していなかったので、入力そのままのものを先に試す
    candidates = [password.encode("utf-8")]
    normalized = _password_bytes(password)
    if normalized not in candidates:
        candidates.append(normalized)

    for pwd in candidates:
        key = base64.urlsafe_b64encode(_derive_key(pwd, salt, LEGACY_ITERATIONS))
        try:
            return Fernet(key).decrypt(token)
        except InvalidToken:
            continue
    raise DecryptionError()


def decrypt_file(
    file_path: str,
    password: str,
    output_path: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> str:
    """暗号化ファイルを復号して保存し、そのパスを返す（旧形式 Fernet にも対応）。

    '<名前>.enc' なら '.enc' を外した名前で保存する。同名のファイルがあっても上書きしない。
    パスワード違いのときは何も作らない。
    """
    _require_file(file_path)
    _password_bytes(password)  # 空のパスワードを早めに弾く
    desired, explicit = _resolve_output(_default_decrypt_path(file_path), output_path)

    out = _AtomicOutput(desired, explicit)
    try:
        if has_encrypted_header(file_path):
            for plain in _iter_plaintext_chunks(file_path, password, progress, is_cancelled):
                out.file.write(plain)
        else:
            _check_cancel(is_cancelled)
            out.file.write(_decrypt_legacy(file_path, password))
            _report(progress, 1, 1)
        return out.commit()
    except BaseException:
        out.abort()
        raise


def verify_file(
    file_path: str,
    password: str,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> None:
    """暗号化ファイルを、何も書き出さずに最後まで復号できるか確かめる（version 1 のみ）。

    暗号化の直後に呼んで「元のファイルを消しても大丈夫か」を確認するために使う。
    """
    _require_file(file_path)
    if not has_encrypted_header(file_path):
        raise InvalidFileError("このアプリの形式 (version 1) の暗号化ファイルではありません。")
    for _ in _iter_plaintext_chunks(file_path, password, progress, is_cancelled):
        pass


# ----------------------------------------------------------------------
# シュレッダー
# ----------------------------------------------------------------------
def shred_file(
    file_path: str,
    passes: int = DEFAULT_SHRED_PASSES,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> None:
    """ファイルをランダムなデータで上書きし、名前も変えてから削除する。

    - シンボリックリンクとフォルダは対象外（リンク先を壊さないため）
    - 読み取り専用のファイルも処理する
    - SSD やクラウド同期などでは完全な消去は保証できない（モジュールの説明を参照）
    - キャンセルすると、上書きの途中でファイルが残る（中身はすでに壊れている）
    """
    if os.path.islink(file_path):
        raise ValueError("シンボリックリンクは対象外です（リンク先のファイルを直接指定してください）。")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"ファイルが見つかりません: {file_path}")
    if passes < 1:
        raise ValueError("上書き回数は 1 以上にしてください")

    if not os.access(file_path, os.W_OK):
        os.chmod(file_path, stat.S_IREAD | stat.S_IWRITE)  # 読み取り専用属性を外す

    size = os.path.getsize(file_path)
    grand_total = size * passes

    with open(file_path, "r+b") as f:
        for pass_index in range(passes):
            f.seek(0)
            written = 0
            while written < size:
                _check_cancel(is_cancelled)
                n = min(SHRED_CHUNK_SIZE, size - written)
                f.write(os.urandom(n))
                written += n
                _report(progress, pass_index * size + written, grand_total)
            f.flush()
            os.fsync(f.fileno())

    target = file_path
    scrambled = os.path.join(os.path.dirname(os.path.abspath(file_path)), "." + os.urandom(8).hex())
    try:
        os.rename(file_path, scrambled)
        target = scrambled
    except OSError:
        pass
    os.remove(target)

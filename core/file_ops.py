from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

try:  
    from send2trash import send2trash as _send2trash
except ImportError:  # pragma: no cover - 環境による
    _send2trash = None

TRASH_AVAILABLE = _send2trash is not None

COPY_CHUNK_SIZE = 1024 * 1024  # チャンクコピーの読み書き単位 (1 MiB)


class OperationCancelled(Exception):
    """コピー / 移動が途中でキャンセルされた (宛先に中途半端なファイルは残らない)"""


@dataclass
class OperationResult:

    succeeded: list = field(default_factory=list)  # 成功した項目（宛先、または対象パス）
    failed: list = field(default_factory=list)  # (パス, 理由) のタプル
    cancelled: bool = False

    @property
    def success_count(self) -> int:
        return len(self.succeeded)


# ----------------------------------------------------------------------
# パスのヘルパー
# ----------------------------------------------------------------------
def is_root(path: str) -> bool:
    """ドライブ直下（C:\\ や /）かどうか"""
    norm = os.path.normpath(path)
    return os.path.dirname(norm) == norm


def _name_of(path: str) -> str:
    return os.path.basename(os.path.normpath(path))


def _is_inside(path: str, parent: str) -> bool:
    """path が parent と同じ、または parent の内側にあるか"""
    parent_abs = os.path.normcase(os.path.abspath(parent))
    path_abs = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([parent_abs, path_abs]) == parent_abs
    except ValueError:  # ドライブが異なる
        return False


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _require_exists(path: str) -> None:
    if not os.path.lexists(path):
        raise FileNotFoundError(f"見つかりません: {path}")


def unique_path(path: str) -> str:
    """既存の項目と衝突しないパスを返す（"name (2).ext", "name (3).ext", ...）"""
    if not os.path.lexists(path):
        return path
    if os.path.isdir(path):
        base, ext = path, ""
    else:
        base, ext = os.path.splitext(path)
    n = 2
    while True:
        candidate = f"{base} ({n}){ext}"
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def format_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size} bytes"


# ----------------------------------------------------------------------
# 単一項目の操作
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 新規作成
# ----------------------------------------------------------------------
_INVALID_NAME_CHARS = set('\\/:*?"<>|')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


def validate_item_name(name: str) -> str:
    """新規作成用の名前を検証し、整えた名前を返す。使えない名前は ValueError。"""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("名前が空です")
    if len(cleaned) > 255:
        raise ValueError("名前が長すぎます (255文字まで)")
    if cleaned in (".", ".."):
        raise ValueError(f"その名前は使えません: {name}")
    if any(c in _INVALID_NAME_CHARS or ord(c) < 32 for c in cleaned):
        raise ValueError('名前に \\ / : * ? " < > | は使えません')
    if cleaned.endswith("."):
        raise ValueError("名前の末尾にドットは使えません")
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        raise ValueError(f"その名前は予約されているため使えません: {cleaned}")
    return cleaned


def create_folder(dest_dir: str, name: str = "新しいフォルダ") -> str:
    """dest_dir の中に空のフォルダを作り、そのパスを返す。同名があれば別名にする。"""
    if not os.path.isdir(dest_dir):
        raise FileNotFoundError(f"作成先のフォルダが見つかりません: {dest_dir}")
    path = unique_path(os.path.join(dest_dir, validate_item_name(name)))
    os.mkdir(path)
    return path


def create_text_file(dest_dir: str, name: str = "新しいテキストファイル.txt") -> str:
    """dest_dir の中に空のテキストファイルを作り、そのパスを返す。同名があれば別名にする。"""
    if not os.path.isdir(dest_dir):
        raise FileNotFoundError(f"作成先のフォルダが見つかりません: {dest_dir}")
    path = unique_path(os.path.join(dest_dir, validate_item_name(name)))
    with open(path, "x", encoding="utf-8"):
        pass
    return path


# ----------------------------------------------------------------------
# チャンクコピー (進捗・キャンセル対応)
# ----------------------------------------------------------------------
ProgressCallback = Callable[[int, int], None]  # (処理済みバイト数, 全体バイト数)
CancelCallback = Callable[[], bool]


def _check_cancelled(is_cancelled: Optional[CancelCallback]) -> None:
    if is_cancelled is not None and is_cancelled():
        raise OperationCancelled("キャンセルされました")


def _on_rmtree_error(func, path, _exc_info) -> None:
    """rmtree のエラーハンドラ: 読み取り専用属性を外して再試行する (主に Windows)。"""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    func(path)


def _rmtree(path: str) -> None:
    """読み取り専用ファイルが混ざっていても止まらない shutil.rmtree。"""
    try:
        shutil.rmtree(path, onexc=_on_rmtree_error)
    except TypeError:  # Python 3.11 以前は onexc が無い
        shutil.rmtree(path, onerror=_on_rmtree_error)


def _remove_file(path: str) -> None:
    """読み取り専用ファイルでも消せる os.remove。"""
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.remove(path)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _copy_file_chunked(
    src: str,
    dest: str,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
    done_offset: int = 0,
    total_override: Optional[int] = None,
) -> None:
    """src を dest へチャンクコピーする。一時ファイルに書いて、成功したときだけ dest にする。

    progress には (done_offset + 処理済み, total_override or ファイルサイズ) を渡す
    (フォルダコピー全体での累計進捗に使うためのオフセット)。
    キャンセル・失敗時は一時ファイルを消し、dest は作られない。
    """
    total = total_override if total_override is not None else os.path.getsize(src)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)), prefix=".ngf-", suffix=".tmp")
    done = 0
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            while True:
                _check_cancelled(is_cancelled)
                chunk = inp.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done_offset + done, total)
            out.flush()
            os.fsync(out.fileno())
        shutil.copystat(src, tmp_path)
        os.replace(tmp_path, dest)
    except BaseException:
        _remove_quietly(tmp_path)
        raise
    if progress is not None and done == 0:
        progress(done_offset, total)


def _folder_total_size(root: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            fp = os.path.join(dirpath, name)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def _copy_tree_chunked(
    src: str,
    dest: str,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> None:
    """フォルダをチャンクコピーする。失敗・キャンセル時は作りかけの dest を丸ごと消す。"""
    total = _folder_total_size(src)
    done = 0
    try:
        for dirpath, dirs, files in os.walk(src):
            rel = os.path.relpath(dirpath, src)
            target_dir = dest if rel == "." else os.path.join(dest, rel)
            os.makedirs(target_dir, exist_ok=True)
            for d in list(dirs):
                full = os.path.join(dirpath, d)
                if os.path.islink(full):  # フォルダへのリンクはリンクとして複製する
                    os.symlink(os.readlink(full), os.path.join(target_dir, d))
                    dirs.remove(d)
            for name in files:
                _check_cancelled(is_cancelled)
                full = os.path.join(dirpath, name)
                target = os.path.join(target_dir, name)
                if os.path.islink(full):
                    os.symlink(os.readlink(full), target)
                    continue
                size = os.path.getsize(full)
                _copy_file_chunked(full, target, progress, is_cancelled, done, total)
                done += size
        shutil.copystat(src, dest)
    except BaseException:
        try:
            _rmtree(dest)
        except OSError:
            pass
        raise
    if progress is not None and total == 0:
        progress(0, 0)


def copy_item(
    src: str,
    dest_dir: str,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> str:
    """src を dest_dir の中へコピーし、コピー先のパスを返す。同名があれば別名にする。

    progress / is_cancelled を渡すと、大きなファイルでも進捗報告と途中キャンセルができる。
    """
    _require_exists(src)
    name = _name_of(src)
    if not name:
        raise ValueError("ドライブ全体はコピーできません")
    if os.path.isdir(src) and _is_inside(dest_dir, src):
        raise ValueError("フォルダを自分自身の中にはコピーできません")

    dest = unique_path(os.path.join(dest_dir, name))
    if os.path.isdir(src) and not os.path.islink(src):
        _copy_tree_chunked(src, dest, progress, is_cancelled)
    elif os.path.islink(src):
        shutil.copy2(src, dest, follow_symlinks=False)
    else:
        _copy_file_chunked(src, dest, progress, is_cancelled)
    return dest


def move_item(
    src: str,
    dest_dir: str,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> str:
    """src を dest_dir の中へ移動し、移動先のパスを返す。同名があれば別名にする。

    同じドライブ内は名前の変更だけで済ませ、別ドライブ間はチャンクコピー + 削除で
    進捗報告と途中キャンセルに対応する (キャンセル時は src がそのまま残る)。
    """
    _require_exists(src)
    if is_root(src):
        raise ValueError("ドライブ全体は移動できません")
    if _same_dir(os.path.dirname(os.path.abspath(src)), dest_dir):
        return src  # 同じフォルダへの移動は何もしない
    if os.path.isdir(src) and _is_inside(dest_dir, src):
        raise ValueError("フォルダを自分自身の中には移動できません")

    dest = unique_path(os.path.join(dest_dir, _name_of(src)))
    try:
        same_device = os.stat(src).st_dev == os.stat(dest_dir).st_dev
    except OSError:
        same_device = False
    if same_device:
        shutil.move(src, dest)
        return dest

    # 別ドライブ間: コピーが完了してから元を消す
    if os.path.isdir(src) and not os.path.islink(src):
        _copy_tree_chunked(src, dest, progress, is_cancelled)
        _rmtree(src)
    elif os.path.islink(src):
        shutil.copy2(src, dest, follow_symlinks=False)
        _remove_file(src)
    else:
        _copy_file_chunked(src, dest, progress, is_cancelled)
        _remove_file(src)
    return dest


def delete_item(path: str, permanent: bool = False) -> None:
    """path を削除する。permanent=False ならごみ箱へ（send2trash が必要）。"""
    _require_exists(path)
    if is_root(path):
        raise ValueError("ドライブ全体は削除できません")

    if not permanent:
        if _send2trash is None:
            raise RuntimeError("send2trash が未インストールのため、ごみ箱へ移動できません")
        _send2trash(os.path.normpath(path))
        return

    if os.path.isdir(path) and not os.path.islink(path):
        _rmtree(path)
    else:
        _remove_file(path)


# ----------------------------------------------------------------------
# 一括実行
# ----------------------------------------------------------------------
def _describe_error(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def run_batch(
    paths,
    action: Callable[[str], Optional[str]],
    progress: Optional[Callable[[int, int, str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> OperationResult:
    """paths の各項目に action を適用する。1件が失敗しても残りは続行し、結果を集めて返す。

    progress(index, total, name) は各項目の処理前に呼ばれる。
    is_cancelled() が True を返したら、次の項目に進まずに中断する。
    """
    paths = list(paths)
    total = len(paths)
    result = OperationResult()

    for index, path in enumerate(paths):
        if is_cancelled is not None and is_cancelled():
            result.cancelled = True
            break
        if progress is not None:
            progress(index, total, os.path.basename(os.path.normpath(path)) or path)
        try:
            outcome = action(path)
        except OperationCancelled:
            result.cancelled = True
            break
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない
            result.failed.append((path, _describe_error(exc)))
        else:
            result.succeeded.append(outcome or path)

    return result

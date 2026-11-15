from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional

try:  
    from send2trash import send2trash as _send2trash
except ImportError:  # pragma: no cover - 環境による
    _send2trash = None

TRASH_AVAILABLE = _send2trash is not None


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
    if size > 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size > 1024 ** 2:
        return f"{size / 1024 ** 2:.2f} MB"
    if size > 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size} bytes"


# ----------------------------------------------------------------------
# 単一項目の操作
# ----------------------------------------------------------------------
def copy_item(src: str, dest_dir: str) -> str:
    """src を dest_dir の中へコピーし、コピー先のパスを返す。同名があれば別名にする。"""
    _require_exists(src)
    name = _name_of(src)
    if not name:
        raise ValueError("ドライブ全体はコピーできません")
    if os.path.isdir(src) and _is_inside(dest_dir, src):
        raise ValueError("フォルダを自分自身の中にはコピーできません")

    dest = unique_path(os.path.join(dest_dir, name))
    if os.path.isdir(src):
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest)
    return dest


def move_item(src: str, dest_dir: str) -> str:
    """src を dest_dir の中へ移動し、移動先のパスを返す。同名があれば別名にする。"""
    _require_exists(src)
    if is_root(src):
        raise ValueError("ドライブ全体は移動できません")
    if _same_dir(os.path.dirname(os.path.abspath(src)), dest_dir):
        return src  # 同じフォルダへの移動は何もしない
    if os.path.isdir(src) and _is_inside(dest_dir, src):
        raise ValueError("フォルダを自分自身の中には移動できません")

    dest = unique_path(os.path.join(dest_dir, _name_of(src)))
    shutil.move(src, dest)
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
        shutil.rmtree(path)
    else:
        os.remove(path)


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
        except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない
            result.failed.append((path, _describe_error(exc)))
        else:
            result.succeeded.append(outcome or path)

    return result

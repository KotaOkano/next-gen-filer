
from __future__ import annotations

import datetime
import os
import re
import struct
import subprocess
import sys

from PySide6.QtCore import (
    QByteArray,
    QDir,
    QEvent,
    QMimeData,
    QModelIndex,
    QSettings,
    QSortFilterProxyModel,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QDrag,
    QGuiApplication,
    QKeyEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:  # PySide6 のバージョンによって、QFileSystemModel の所属モジュールが異なる
    from PySide6.QtGui import QFileSystemModel
except ImportError:
    from PySide6.QtWidgets import QFileSystemModel

from core import file_ops
from core.size_worker import FolderSizeWorker
from ui.batch_rename_dialog import BatchRenameDialog
from ui.hash_dialog import HashDialog
from ui.quick_look_dialog import QuickLookDialog

# ----------------------------------------------------------------------
# 定数
# ----------------------------------------------------------------------
PC_LABEL = "PC (ドライブ一覧)"
STATUS_DEFAULT = "項目を選択してください"

# Windows エクスプローラーと「切り取り / コピー」を共有するためのクリップボード形式
DROP_EFFECT_FORMAT = "Preferred DropEffect"
DROP_EFFECT_COPY = 1
DROP_EFFECT_MOVE = 2

OPERATION_LABELS = {
    "copy": "コピー",
    "move": "移動",
    "trash": "ごみ箱へ移動",
    "delete": "完全削除",
}

QUICK_BUTTON_STYLE = """
    QPushButton {
        background-color: #2d2d2d;
        color: #ffffff;
        border: 1px solid #444444;
        border-radius: 3px;
        padding: 4px 8px;
        font-size: 11px;
    }
    QPushButton:hover {
        background-color: #3d3d3d;
        border: 1px solid #666666;
    }
    QPushButton:pressed {
        background-color: #1a1a1a;
    }
"""

SEARCH_INPUT_STYLE = """
    QLineEdit {
        background-color: #1e1e1e;
        color: #ffffff;
        border: 1px solid #444444;
        border-radius: 3px;
        padding: 4px;
        font-size: 12px;
    }
    QLineEdit:focus {
        border: 1px solid #007acc;
    }
"""

STATUS_STYLE = "color: #aaa; padding: 4px; font-size: 12px;"

# 実行中の FolderSizeWorker を保持する (スレッド実行中に破棄されないように)
_LIVE_SIZE_WORKERS: list = []


def start_folder_size_worker(path: str, callback):
    """フォルダの合計サイズを別スレッドで数え、callback(path, size) で返す。"""
    worker = FolderSizeWorker(path)
    _LIVE_SIZE_WORKERS.append(worker)

    def _done(done_path, size):
        if worker in _LIVE_SIZE_WORKERS:
            _LIVE_SIZE_WORKERS.remove(worker)
        worker.deleteLater()
        callback(done_path, size)

    worker.finished.connect(_done)
    worker.start()
    return worker


# ----------------------------------------------------------------------
# パスのヘルパー
# ----------------------------------------------------------------------
def native(path: str) -> str:
    return QDir.toNativeSeparators(path)


def normalize_path(path: str) -> str:
    return QDir.cleanPath(path) if path else ""


def parse_user_path(text: str) -> str:
    text = text.strip().strip('"').strip()
    if not text or text == PC_LABEL:
        return ""
    return normalize_path(os.path.expandvars(os.path.expanduser(text)))


# ----------------------------------------------------------------------
# ZIP アーカイブ表示用の仮想パス
# ----------------------------------------------------------------------
# アーカイブ表示中のタブは、現在地を "<ZIP の実パス>!<ZIP 内パス>" という
# 仮想パスで表す (例: "C:/data/a.zip!/sub")。履歴やタブ復元はこの文字列で扱う。
def archive_virtual_path(archive: str, inner: str = "") -> str:
    inner = (inner or "").strip("/")
    return f"{archive}!/{inner}" if inner else f"{archive}!"


def split_archive_path(path: str):
    """仮想パスなら (ZIP の実パス, ZIP 内パス) を返す。通常のパスなら None。

    "!" を含む通常のパスと誤判定しないよう、前半が実在する .zip ファイルの
    ときだけ仮想パスとみなす。
    """
    if not path or "!" not in path:
        return None
    start = 0
    while True:
        idx = path.find("!", start)
        if idx < 0:
            return None
        head = path[:idx]
        if head.lower().endswith(".zip") and os.path.isfile(head):
            return head, path[idx + 1:].strip("/")
        start = idx + 1


def tab_title(path: str) -> str:
    if not path:
        return "PC"
    parsed = split_archive_path(path)
    if parsed is not None:
        archive, inner = parsed
        return os.path.basename(inner) if inner else os.path.basename(archive)
    return os.path.basename(path.rstrip("/")) or path


# ----------------------------------------------------------------------
# プロキシモデル（絞り込み＋ソート）
# ----------------------------------------------------------------------
_NATURAL_SPLIT = re.compile(r"(\d+)")


def _natural_key(text: str) -> list:
    return [int(part) if i % 2 else part.casefold() for i, part in enumerate(_NATURAL_SPLIT.split(text))]


class FileSortFilterProxyModel(QSortFilterProxyModel):



    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(0)  # ファイル名の列で絞り込む

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if model is None:
            return True
        index = model.index(source_row, 0, source_parent)
        if model.isDir(index):  # ディスクに触らず、モデルのキャッシュで判定する
            return True
        return super().filterAcceptsRow(source_row, source_parent)

    def lessThan(self, left, right):
        model = self.sourceModel()
        if model is None:
            return super().lessThan(left, right)

        left_dir, right_dir = model.isDir(left), model.isDir(right)
        if left_dir != right_dir:
            # プロキシは降順のとき比較結果を反転するので、フォルダを常に先頭に保つには逆にする
            return left_dir if self.sortOrder() == Qt.AscendingOrder else not left_dir

        column = self.sortColumn()
        if column == 1 and not left_dir:  # サイズ
            a, b = model.size(left), model.size(right)
            if a != b:
                return a < b
        elif column == 2:  # 種類
            a, b = model.type(left).casefold(), model.type(right).casefold()
            if a != b:
                return a < b
        elif column == 3:  # 更新日時
            a, b = model.lastModified(left), model.lastModified(right)
            if a != b:
                return a < b

        return _natural_key(model.fileName(left)) < _natural_key(model.fileName(right))


# アーカイブ表示モデルのアイテム (名前の列) に持たせるデータの役割
ROLE_IS_DIR = Qt.UserRole
ROLE_SIZE = Qt.UserRole + 1
ROLE_MODIFIED = Qt.UserRole + 2
ROLE_INNER = Qt.UserRole + 3
ROLE_TYPE = Qt.UserRole + 4


class ArchiveSortFilterProxyModel(QSortFilterProxyModel):
    """ZIP 内一覧用のプロキシ (フォルダを常に先頭にし、サイズ・日時を値で並べ替える)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(0)  # ファイル名の列で絞り込む

    @staticmethod
    def _name_item(model, row):
        return model.item(row, 0) if model is not None else None

    def filterAcceptsRow(self, source_row, source_parent):
        item = self._name_item(self.sourceModel(), source_row)
        if item is not None and item.data(ROLE_IS_DIR):
            return True  # フォルダは絞り込み中も常に表示する
        return super().filterAcceptsRow(source_row, source_parent)

    def lessThan(self, left, right):
        model = self.sourceModel()
        li = self._name_item(model, left.row())
        ri = self._name_item(model, right.row())
        if li is None or ri is None:
            return super().lessThan(left, right)

        left_dir, right_dir = bool(li.data(ROLE_IS_DIR)), bool(ri.data(ROLE_IS_DIR))
        if left_dir != right_dir:
            # フォルダを常に先頭に保つ (FileSortFilterProxyModel と同じ考え方)
            return left_dir if self.sortOrder() == Qt.AscendingOrder else not left_dir

        column = self.sortColumn()
        if column == 1:  # サイズ
            a, b = li.data(ROLE_SIZE) or 0, ri.data(ROLE_SIZE) or 0
            if a != b:
                return a < b
        elif column == 2:  # 種類
            a, b = (li.data(ROLE_TYPE) or "").casefold(), (ri.data(ROLE_TYPE) or "").casefold()
            if a != b:
                return a < b
        elif column == 3:  # 更新日時
            a, b = li.data(ROLE_MODIFIED), ri.data(ROLE_MODIFIED)
            a = -1.0 if a is None else a
            b = -1.0 if b is None else b
            if a != b:
                return a < b

        return _natural_key(li.text()) < _natural_key(ri.text())


# ----------------------------------------------------------------------
# テーブルビュー（キー操作・右クリックメニュー）
# ----------------------------------------------------------------------
class InteractiveTableView(QTableView):
    """キー操作と右クリックメニューを処理するテーブルビュー"""

    def __init__(self, pane: "PaneWidget", parent=None):
        super().__init__(parent)
        self.pane = pane
        self._drag_start_pos = None

        # ダブルクリック等による勝手な編集を防ぐ（名前変更は F2 / メニューから明示的に開始する）
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        # ドラッグ＆ドロップ (ドラッグ開始は mouseMoveEvent で自前処理する)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

        # (キー, 修飾キー, 実行する処理)
        self._shortcuts = [
            (Qt.Key_F2, Qt.NoModifier, self.rename_current),
            (Qt.Key_Return, Qt.ControlModifier, self.rename_current),
            (Qt.Key_Space, Qt.NoModifier, pane.open_quick_look),
            (Qt.Key_Return, Qt.NoModifier, pane.open_selected_item),
            (Qt.Key_Enter, Qt.NoModifier, pane.open_selected_item),
            (Qt.Key_R, Qt.ControlModifier, pane.open_batch_rename),
            (Qt.Key_C, Qt.ControlModifier, pane.copy_to_clipboard),
            (Qt.Key_X, Qt.ControlModifier, pane.cut_to_clipboard),
            (Qt.Key_V, Qt.ControlModifier, pane.paste_items),
            (Qt.Key_F, Qt.ControlModifier, pane.focus_search_bar),
            (Qt.Key_L, Qt.ControlModifier, pane.focus_path_bar),
            (Qt.Key_F5, Qt.NoModifier, pane.copy_items),
            (Qt.Key_F6, Qt.NoModifier, pane.move_items),
            (Qt.Key_F8, Qt.NoModifier, pane.delete_items),
            (Qt.Key_Delete, Qt.NoModifier, pane.delete_items),
            (Qt.Key_Delete, Qt.ShiftModifier, pane.delete_permanently),
            (Qt.Key_Left, Qt.AltModifier, pane.go_back),
            (Qt.Key_Right, Qt.AltModifier, pane.go_forward),
            (Qt.Key_N, Qt.ControlModifier | Qt.ShiftModifier, pane.create_new_folder),
            (Qt.Key_W, Qt.ControlModifier, pane.close_current_tab),
            (Qt.Key_H, Qt.ControlModifier, pane.toggle_hidden_files),
            (Qt.Key_Backspace, Qt.NoModifier, pane.go_parent_directory),
            (Qt.Key_Up, Qt.AltModifier, pane.go_parent_directory),
        ]

    # ---- キー操作 ----------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        # Ctrl+Tab / Ctrl+Shift+Tab はタブの切り替え (Shift+Tab は Backtab で届く)
        if event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            if event.key() == Qt.Key_Backtab or event.modifiers() & Qt.ShiftModifier:
                self.pane.activate_previous_tab()
            else:
                self.pane.activate_next_tab()
            event.accept()
            return

        modifiers = event.modifiers()
        if modifiers == Qt.KeypadModifier:  # テンキーの Enter など
            modifiers = Qt.NoModifier

        for key, modifier, handler in self._shortcuts:
            if event.key() == key and modifiers == modifier:
                handler()
                event.accept()
                return
        super().keyPressEvent(event)

    # ---- 名前の変更 --------------------------------------------------
    def _tab(self) -> "FileTab | None":
        parent = self.parent()
        return parent if isinstance(parent, FileTab) else None

    def _path_of(self, index: QModelIndex) -> str:
        tab = self._tab()
        if tab is not None:
            return tab.path_at(index)
        proxy = self.model()
        return proxy.sourceModel().filePath(proxy.mapToSource(index))

    def rename_index(self, index: QModelIndex):
        if not index.isValid():
            return
        tab = self._tab()
        if tab is not None and tab.in_archive:
            self.pane.set_status("アーカイブ内では名前を変更できません")
            return
        index = index.siblingAtColumn(0)  # 名前の列だけが編集できる
        path = self._path_of(index)
        if not path or file_ops.is_root(path):  # ドライブ名は変更しない
            return
        self.edit(index)

    def rename_current(self):
        self.rename_index(self.currentIndex())

    # ---- ドラッグ＆ドロップ ------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.LeftButton
            and self.state() != QAbstractItemView.EditingState
            and (event.pos() - self._drag_start_pos).manhattanLength() >= 10
        ):
            self._drag_start_pos = None
            self._start_drag(event.modifiers())
            return
        super().mouseMoveEvent(event)

    def _start_drag(self, modifiers):
        tab = self.pane.current_tab()
        if tab is not None and tab.in_archive:
            return  # アーカイブ内の項目はドラッグできない (読み取り専用)
        paths = tab.selected_paths() if tab else []
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        # 既定はコピー、Shift を押しながらなら移動 (修飾キーはドロップ先が最終判断する)
        default = Qt.MoveAction if modifiers & Qt.ShiftModifier else Qt.CopyAction
        drag.exec(Qt.CopyAction | Qt.MoveAction, default)

    @staticmethod
    def _drop_action(event) -> Qt.DropAction:
        """Shift を押しながらなら移動、それ以外はコピー。"""
        if event.keyboardModifiers() & Qt.ShiftModifier and event.possibleActions() & Qt.MoveAction:
            return Qt.MoveAction
        return Qt.CopyAction

    def _drop_target_dir(self, pos):
        """ドロップ先フォルダを返す。フォルダ行の上ならそのフォルダ、それ以外は表示中のフォルダ。"""
        tab = self.pane.current_tab()
        if tab is None or tab.in_archive:
            return None  # アーカイブ内へはドロップできない
        index = self.indexAt(pos)
        if index.isValid():
            path = self._path_of(index)
            if path and os.path.isdir(path):
                return normalize_path(path)
        return tab.current_path or None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._drop_target_dir(event.pos()) is not None:
            event.setDropAction(self._drop_action(event))
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and self._drop_target_dir(event.pos()) is not None:
            event.setDropAction(self._drop_action(event))
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        dest_dir = self._drop_target_dir(event.pos())
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if dest_dir is None or not paths:
            event.ignore()
            return
        move = self._drop_action(event) == Qt.MoveAction
        event.setDropAction(Qt.MoveAction if move else Qt.CopyAction)
        event.accept()
        self.pane.accept_drop(paths, dest_dir, move)

    # ---- 右クリックメニュー ------------------------------------------
    def _show_archive_context_menu(self, pos, tab):
        """アーカイブ表示中の右クリックメニュー (読み取り専用なので開く操作だけ)。"""
        pane = self.pane
        index = self.indexAt(pos)
        menu = QMenu(self)

        def add(text, slot):
            action = QAction(text, menu)
            action.triggered.connect(lambda _checked=False: slot())
            menu.addAction(action)

        if index.isValid():
            add("↗️開く / 実行 (Enter)", pane.open_selected_item)
            menu.addSeparator()
        add("🗜️ すべて展開 (ZIP のあるフォルダへ)", lambda: pane.extract_archive_to_parent(tab.archive_path))
        add("📁 アーカイブを閉じる", pane.leave_archive)
        menu.exec(self.viewport().mapToGlobal(pos))

    def show_context_menu(self, pos):
        pane = self.pane
        tab = self._tab()
        if tab is not None and tab.in_archive:
            self._show_archive_context_menu(pos, tab)
            return
        index = self.indexAt(pos)
        menu = QMenu(self)

        def add(text, slot):
            action = QAction(text, menu)
            action.triggered.connect(lambda _checked=False: slot())
            menu.addAction(action)

        def add_hidden_toggle():
            action = QAction("隠しファイルを表示 (Ctrl+H)", menu)
            action.setCheckable(True)
            action.setChecked(pane.show_hidden)
            action.triggered.connect(lambda _checked=False: pane.toggle_hidden_files())
            menu.addAction(action)

        if not index.isValid():
            add("📁 新しいフォルダ (Ctrl+Shift+N)", pane.create_new_folder)
            add("📄 新しいテキストファイル", pane.create_new_text_file)
            menu.addSeparator()
            add("貼り付け (Ctrl+V)", pane.paste_items)
            menu.addSeparator()
            add_hidden_toggle()
        else:
            path = self._path_of(index)

            add("↗️開く / 実行 (Enter)", pane.open_selected_item)
            add("👁️クイックルック (Space)", pane.open_quick_look)
            add("✏️ 名前の変更 (F2)", lambda: self.rename_index(index))
            add("🏷️一括リネーム (Ctrl+R)", pane.open_batch_rename)
            menu.addSeparator()

            add("✂️切り取り (Ctrl+X)", pane.cut_to_clipboard)
            add("📋コピー (Ctrl+C)", pane.copy_to_clipboard)
            add("📋貼り付け (Ctrl+V)", pane.paste_items)
            menu.addSeparator()

            add("反対ペインへコピー (F5)", pane.copy_items)
            add("反対ペインへ移動 (F6)", pane.move_items)
            menu.addSeparator()

            if file_ops.TRASH_AVAILABLE:
                add("🗑️ごみ箱へ移動 (Delete / F8)", pane.delete_items)
                add("🗑️完全に削除 (Shift+Delete)", pane.delete_permanently)
            else:
                add("🗑️削除 (Delete / F8)", pane.delete_items)
            menu.addSeparator()

            add("エクスプローラーで開く", lambda: pane.open_in_explorer(path))
            menu.addSeparator()

            add("📋 パスをコピー", lambda: pane.copy_path_to_clipboard(path))
            add("ℹ️ プロパティ", lambda: pane.show_properties(path))
            selected = pane.current_tab().selected_paths() if pane.current_tab() else []
            if len(selected) == 1 and os.path.isfile(path):
                add("🔑 ハッシュを計算", pane.open_hash_dialog)
            menu.addSeparator()

            add("🗜️ ZIPに圧縮", pane.compress_selected_to_zip)
            if path.lower().endswith(".zip"):
                add("🗜️ ZIPを展開 (ここに展開)", lambda: pane.extract_zip_archive(path))
            menu.addSeparator()

            add_hidden_toggle()
            menu.addSeparator()
            add("📁 新しいフォルダ (Ctrl+Shift+N)", pane.create_new_folder)
            add("📄 新しいテキストファイル", pane.create_new_text_file)

        menu.exec(self.viewport().mapToGlobal(pos))


# ----------------------------------------------------------------------
# 1タブ分
# ----------------------------------------------------------------------
class FileTab(QWidget):
    """1タブ分のファイル一覧。モデル / プロキシ / ビューを所有し、パスの変換を引き受ける。"""

    selection_changed = Signal()

    def __init__(self, pane: "PaneWidget", path: str = "", parent=None):
        super().__init__(parent)
        self._path = ""
        self.filter_text = ""
        self._history: list = []
        self._history_index = -1

        self.model = QFileSystemModel(self)
        self.model.setReadOnly(False)  # 名前の変更を許可する（編集できるのは名前の列だけ）
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs)

        self.proxy = FileSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)

        # ZIP アーカイブ表示用 (通常表示とビューに差し込むモデルを切り替えて使う)
        self._archive: "str | None" = None
        self._inner = ""
        self._archive_entries: dict = {}  # ZIP 内パス -> フォルダか (現在の階層分)
        self.archive_model = QStandardItemModel(self)
        self.archive_model.setHorizontalHeaderLabels(["名前", "サイズ", "種類", "更新日時"])
        self.archive_proxy = ArchiveSortFilterProxyModel(self)
        self.archive_proxy.setSourceModel(self.archive_model)

        self.view = InteractiveTableView(pane, self)
        self.view.setModel(self.proxy)
        self._configure_view()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.view.selectionModel().selectionChanged.connect(self._emit_selection_changed)
        self.navigate(path)

    def _configure_view(self):
        view = self.view
        view.setSelectionBehavior(QTableView.SelectRows)
        view.setSelectionMode(QTableView.ExtendedSelection)
        view.setAlternatingRowColors(True)
        view.setSortingEnabled(True)
        view.sortByColumn(0, Qt.AscendingOrder)
        view.verticalHeader().setVisible(False)  # 行番号は不要
        for column, width in enumerate((300, 100, 120, 150)):
            view.setColumnWidth(column, width)

    def _emit_selection_changed(self, *_args):
        self.selection_changed.emit()

    # ---- ナビゲーション ----------------------------------------------
    @property
    def current_path(self) -> str:
        """表示中のフォルダ（"/" 区切り）。PC のドライブ一覧のときは空文字。"""
        return self._path

    def navigate(self, path: str):
        path = normalize_path(path)
        self._apply_path(path)
        if self._history_index >= 0 and self._history[self._history_index] == path:
            return  # 同じフォルダへの移動は履歴に積まない
        del self._history[self._history_index + 1:]
        self._history.append(path)
        self._history_index = len(self._history) - 1

    def _apply_path(self, path: str):
        parsed = split_archive_path(path)
        if parsed is not None:
            archive, inner = parsed
            self._apply_archive(archive, inner)
            self._path = archive_virtual_path(archive, inner)
            return
        if self._archive is not None:
            # アーカイブ表示から通常表示へ戻る
            self._archive = None
            self._inner = ""
            self._archive_entries = {}
            self._use_fs_view()
        self.model.setRootPath(path)
        if path:
            self.view.setRootIndex(self.proxy.mapFromSource(self.model.index(path)))
        else:
            self.view.setRootIndex(QModelIndex())
        self._path = path

    # ---- アーカイブ表示 ------------------------------------------------
    @property
    def in_archive(self) -> bool:
        return self._archive is not None

    @property
    def archive_path(self) -> "str | None":
        return self._archive

    @property
    def archive_inner(self) -> str:
        return self._inner

    def archive_entry_is_dir(self, inner: str) -> bool:
        """アーカイブ表示中、ZIP 内パス inner がフォルダなら True。"""
        return bool(self._archive_entries.get(inner))

    def _set_view_proxy(self, proxy):
        if self.view.model() is proxy:
            return
        old_selection = self.view.selectionModel()
        if old_selection is not None:
            try:
                old_selection.selectionChanged.disconnect(self._emit_selection_changed)
            except (RuntimeError, TypeError):
                pass  # 接続されていなければ何もしない
        # 切替前の現在位置を消しておく (Qt が旧モデルのインデックスを変換しようとするため)
        self.view.setCurrentIndex(QModelIndex())
        self.view.setRootIndex(QModelIndex())
        self.view.setModel(proxy)
        self.view.selectionModel().selectionChanged.connect(self._emit_selection_changed)

    def _use_archive_view(self):
        self._set_view_proxy(self.archive_proxy)

    def _use_fs_view(self):
        self._set_view_proxy(self.proxy)

    def _apply_archive(self, archive: str, inner: str):
        self._archive = archive
        self._inner = inner
        self._use_archive_view()
        self.view.setRootIndex(QModelIndex())
        try:
            entries = file_ops.list_zip_entries(archive, inner)
        except Exception:  # noqa: BLE001 - 壊れた ZIP などは空表示にする (入る前に検証済み)
            entries = []
        self._fill_archive_model(entries)

    def _fill_archive_model(self, entries):
        self.archive_model.removeRows(0, self.archive_model.rowCount())
        self._archive_entries = {}
        for entry in entries:
            self._archive_entries[entry.inner_path] = entry.is_dir
            if entry.is_dir:
                type_text = "フォルダ"
            else:
                ext = os.path.splitext(entry.name)[1].lstrip(".")
                type_text = f"{ext.upper()} ファイル" if ext else "ファイル"

            name_item = QStandardItem(entry.name)
            name_item.setEditable(False)
            name_item.setData(entry.is_dir, ROLE_IS_DIR)
            name_item.setData(0 if entry.is_dir else entry.size, ROLE_SIZE)
            name_item.setData(entry.inner_path, ROLE_INNER)
            name_item.setData(type_text, ROLE_TYPE)
            if entry.modified is not None:
                name_item.setData(entry.modified.timestamp(), ROLE_MODIFIED)

            size_item = QStandardItem("" if entry.is_dir else file_ops.format_size(entry.size))
            size_item.setEditable(False)
            type_item = QStandardItem(type_text)
            type_item.setEditable(False)
            date_item = QStandardItem(
                entry.modified.strftime("%Y/%m/%d %H:%M") if entry.modified is not None else ""
            )
            date_item.setEditable(False)
            self.archive_model.appendRow([name_item, size_item, type_item, date_item])
        header = self.view.horizontalHeader()
        self.archive_proxy.sort(header.sortIndicatorSection(), header.sortIndicatorOrder())

    @staticmethod
    def _is_valid_history_target(target: str) -> bool:
        if not target:
            return True
        if split_archive_path(target) is not None:
            return True
        return os.path.isdir(target)

    @property
    def can_go_back(self) -> bool:
        return self._history_index > 0

    @property
    def can_go_forward(self) -> bool:
        return 0 <= self._history_index < len(self._history) - 1

    def go_back(self) -> "str | None":
        i = self._history_index
        while i > 0:
            i -= 1
            target = self._history[i]
            if self._is_valid_history_target(target):
                self._history_index = i
                self._apply_path(target)
                return target
        return None

    def go_forward(self) -> "str | None":
        i = self._history_index
        while i < len(self._history) - 1:
            i += 1
            target = self._history[i]
            if self._is_valid_history_target(target):
                self._history_index = i
                self._apply_path(target)
                return target
        return None

    def select_and_rename(self, path: str, _retry: bool = False):
        """作成した項目を選択して、そのまま名前変更を開始する (エクスプローラー式)"""
        source_index = self.model.index(path)
        proxy_index = self.proxy.mapFromSource(source_index) if source_index.isValid() else QModelIndex()
        if not proxy_index.isValid():
            if not _retry:  # モデルの反映が遅れている場合は一度だけ少し待って再試行する
                QTimer.singleShot(300, lambda: self.select_and_rename(path, _retry=True))
            return
        self.view.setCurrentIndex(proxy_index)
        self.view.scrollTo(proxy_index)
        self.view.rename_index(proxy_index)

    def set_filter(self, text: str):
        self.filter_text = text
        if self.in_archive:
            self.archive_proxy.setFilterFixedString(text)
        else:
            self.proxy.setFilterFixedString(text)

    # ---- 表示設定 (隠しファイル・列幅・ソート) --------------------------
    def set_show_hidden(self, show: bool):
        filters = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs
        if show:
            filters |= QDir.Hidden
        self.model.setFilter(filters)

    def column_widths(self) -> list:
        header = self.view.horizontalHeader()
        return [header.sectionSize(i) for i in range(4)]

    def apply_column_widths(self, widths):
        for i, width in enumerate(list(widths)[:4]):
            try:
                width = int(width)
            except (TypeError, ValueError):
                continue
            if width > 0:
                self.view.setColumnWidth(i, width)

    def sort_state(self) -> tuple:
        header = self.view.horizontalHeader()
        return header.sortIndicatorSection(), header.sortIndicatorOrder()

    def apply_sort(self, column: int, order):
        if 0 <= column < 4:
            self.view.sortByColumn(column, order)

    # ---- 選択・パスの取得 --------------------------------------------
    def path_at(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        if self.in_archive:
            source = self.archive_proxy.mapToSource(index)
            item = self.archive_model.item(source.row(), 0)
            if item is None:
                return ""
            return archive_virtual_path(self._archive, item.data(ROLE_INNER) or "")
        return self.model.filePath(self.proxy.mapToSource(index))

    def current_item_path(self) -> str:
        return self.path_at(self.view.currentIndex())

    def selected_paths(self) -> list:
        paths = []
        for index in self.view.selectionModel().selectedRows(0):
            path = self.path_at(index)
            if path:
                paths.append(path)
        return paths

    def selection_summary(self) -> tuple:
        """(ファイル数, フォルダ数, ファイルの合計サイズ)。ディスクに触らずモデルの情報を使う。"""
        files = dirs = total = 0
        if self.in_archive:
            for index in self.view.selectionModel().selectedRows(0):
                source = self.archive_proxy.mapToSource(index)
                item = self.archive_model.item(source.row(), 0)
                if item is None:
                    continue
                if item.data(ROLE_IS_DIR):
                    dirs += 1
                else:
                    files += 1
                    total += item.data(ROLE_SIZE) or 0
            return files, dirs, total
        for index in self.view.selectionModel().selectedRows(0):
            source = self.proxy.mapToSource(index)
            if self.model.isDir(source):
                dirs += 1
            else:
                files += 1
                total += self.model.size(source)
        return files, dirs, total


# ----------------------------------------------------------------------
# バックグラウンドでのファイル操作
# ----------------------------------------------------------------------
class FileOperationWorker(QThread):
    """コピー / 移動 / 削除を別スレッドで実行し、UI を固めない。"""

    progressed = Signal(int, int, str)  # 処理中の番号, 全件数, 名前
    overall = Signal(int)  # 全体の進捗 (0-100 %、ファイル内のバイト進捗を含む)
    done = Signal(object)  # file_ops.OperationResult

    def __init__(
        self,
        kind: str,
        paths: list,
        dest_dir: str = "",
        parent=None,
        on_conflict: str = file_ops.CONFLICT_RENAME,
    ):
        super().__init__(parent)
        self.kind = kind
        self.paths = list(paths)
        self.dest_dir = dest_dir
        self.on_conflict = on_conflict
        self._cancelled = False

    def cancel(self):
        """中断する。コピー / 移動はファイルの途中でも止まり、中途半端なファイルは残らない"""
        self._cancelled = True

    def run(self):
        self._index = 0
        total_items = max(1, len(self.paths))

        def byte_progress(done_bytes: int, total_bytes: int):
            fraction = (done_bytes / total_bytes) if total_bytes else 1.0
            self.overall.emit(min(100, int((self._index + fraction) / total_items * 100)))

        def item_progress(index: int, total: int, name: str):
            self._index = index
            self.progressed.emit(index, total, name)
            if total:
                self.overall.emit(int(index / total * 100))

        is_cancelled = lambda: self._cancelled  # noqa: E731
        actions = {
            "copy": lambda p: file_ops.copy_item(p, self.dest_dir, progress=byte_progress, is_cancelled=is_cancelled, on_conflict=self.on_conflict),
            "move": lambda p: file_ops.move_item(p, self.dest_dir, progress=byte_progress, is_cancelled=is_cancelled, on_conflict=self.on_conflict),
            "trash": lambda p: file_ops.delete_item(p, permanent=False),
            "delete": lambda p: file_ops.delete_item(p, permanent=True),
        }
        result = file_ops.run_batch(
            self.paths,
            actions[self.kind],
            progress=item_progress,
            is_cancelled=is_cancelled,
        )
        self.done.emit(result)


class ArchiveWorker(QThread):
    """ZIP 圧縮 / 展開を別スレッドで実行し、UI を固めない。"""

    progressed = Signal(int)  # 全体の進捗 (0-100 %)
    done = Signal(object)  # (成功したか, 圧縮先パス or 展開した項目リスト or エラー文字列)

    def __init__(self, kind: str, paths: list, dest_dir: str, archive: str = "", parent=None):
        super().__init__(parent)
        self.kind = kind  # "compress" | "extract"
        self.paths = list(paths)
        self.dest_dir = dest_dir
        self.archive = archive
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        is_cancelled = lambda: self._cancelled  # noqa: E731

        def progress(done_bytes: int, total_bytes: int):
            self.progressed.emit(int(done_bytes / total_bytes * 100) if total_bytes else 100)

        try:
            if self.kind == "compress":
                result = file_ops.create_zip(
                    self.paths, self.dest_dir, progress=progress, is_cancelled=is_cancelled
                )
            else:
                result = file_ops.extract_zip(
                    self.archive, self.dest_dir, progress=progress, is_cancelled=is_cancelled
                )
        except file_ops.OperationCancelled:
            self.done.emit((False, "キャンセルされました"))
            return
        except Exception as exc:  # noqa: BLE001
            self.done.emit((False, str(exc) or type(exc).__name__))
            return
        self.done.emit((True, result))


class ConflictDialog(QDialog):
    """宛先に同名の項目があるとき、どう扱うかを選ぶダイアログ。"""

    def __init__(self, conflict_paths: list, dest_dir: str, has_folder_conflict: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("名前の衝突")
        self._policy = None

        layout = QVBoxLayout(self)
        listing = "\n".join(
            f"  ・{os.path.basename(os.path.normpath(p)) or p}" for p in conflict_paths[:5]
        )
        if len(conflict_paths) > 5:
            listing += f"\n  …ほか {len(conflict_paths) - 5} 件"
        label = QLabel(
            f"宛先 ({native(dest_dir)}) には、同名の項目が {len(conflict_paths)} 件あります。\n"
            f"どうしますか？（選んだ扱いは今回の操作すべてに適用されます）\n\n{listing}"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._radios = []
        options = [
            (file_ops.CONFLICT_RENAME, "両方残す（自動で別名にする）", True),
            (file_ops.CONFLICT_OVERWRITE, "置き換える（既存の項目は失われます）", False),
        ]
        if has_folder_conflict:
            options.append(
                (file_ops.CONFLICT_MERGE, "フォルダは中身を統合する（マージ。ファイル同士は置き換え）", False)
            )
        options.append((file_ops.CONFLICT_SKIP, "スキップする（既存の項目を残す）", False))
        for policy, text, checked in options:
            radio = QRadioButton(text)
            radio.setChecked(checked)
            self._radios.append((policy, radio))
            layout.addWidget(radio)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        for policy, radio in self._radios:
            if radio.isChecked():
                self._policy = policy
                break
        self.accept()

    def policy(self):
        """選ばれたポリシー。キャンセルされたら None。"""
        return self._policy


class PropertiesDialog(QDialog):
    """項目のプロパティ (簡易版)。フォルダのサイズは別スレッドで数える。"""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("プロパティ")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        is_dir = os.path.isdir(path)
        name = os.path.basename(os.path.normpath(path)) or path
        form.addRow("名前:", QLabel(name))
        form.addRow("パス:", QLabel(native(path)))
        form.addRow("種類:", QLabel("フォルダ" if is_dir else "ファイル"))

        self._size_label = QLabel("計算中..." if is_dir else file_ops.format_size(os.path.getsize(path)))
        form.addRow("サイズ:", self._size_label)

        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            mtime_text = mtime.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            mtime_text = "不明"
        form.addRow("更新日時:", QLabel(mtime_text))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        if is_dir:
            start_folder_size_worker(path, self._on_size_ready)

    def _on_size_ready(self, _path, size):
        self._size_label.setText(f"{file_ops.format_size(size)}（フォルダ内の合計）")


# ----------------------------------------------------------------------
# ペイン本体
# ----------------------------------------------------------------------
class PaneWidget(QWidget):
    """左右独立して動作するデュアルペインの片側"""

    path_changed = Signal(str)  # 表示中のフォルダが変わった (ツリー連動用)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)  # アクティブ表示の枠線を有効にする
        self.opposite_pane: "PaneWidget | None" = None
        self._worker: "FileOperationWorker | None" = None
        self._progress: "QProgressDialog | None" = None
        self._operation = ("", "", False)  # (種類, 宛先, 終了後にクリップボードを空にするか)
        self.pane_id = ""  # QSettings の保存先を分ける ID ("left" / "right")
        self.show_hidden = False
        self._view_widths = None  # 列幅 (全タブ共通・保存対象)
        self._view_sort = None  # (列, 並び順) (全タブ共通・保存対象)
        self._applying_view_settings = False
        self._archive_worker: "ArchiveWorker | None" = None
        self._archive_progress: "QProgressDialog | None" = None
        self._archive_kind = ""
        self.init_ui()

    # ==================================================================
    # UI 構築
    # ==================================================================
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addLayout(self._build_quick_bar())
        layout.addLayout(self._build_nav_bar())
        layout.addLayout(self._build_search_bar())

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self.tab_widget.tabBar().installEventFilter(self)  # 中クリックでタブを閉じる
        layout.addWidget(self.tab_widget)

        self.status_label = QLabel(STATUS_DEFAULT)
        self.status_label.setStyleSheet(STATUS_STYLE)
        layout.addWidget(self.status_label)

        self.add_new_tab("")

    def eventFilter(self, obj, event):
        if (
            obj is self.tab_widget.tabBar()
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MiddleButton
        ):
            index = self.tab_widget.tabBar().tabAt(event.pos())
            if index >= 0:
                self.close_tab(index)
            return True
        return super().eventFilter(obj, event)

    def _make_quick_button(self, label: str, path: str) -> QPushButton:
        button = QPushButton(label)
        button.setStyleSheet(QUICK_BUTTON_STYLE)
        button.clicked.connect(lambda _checked=False, p=path: self.navigate_to_path(p))
        return button

    def _build_quick_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)

        layout.addWidget(self._make_quick_button("🏠 PC", ""))

        # OneDrive でフォルダが移動されていても正しい場所を指す
        locations = (
            ("💻 デスクトップ", QStandardPaths.DesktopLocation),
            ("📄 ドキュメント", QStandardPaths.DocumentsLocation),
            ("⬇ ダウンロード", QStandardPaths.DownloadLocation),
        )
        for label, location in locations:
            path = QStandardPaths.writableLocation(location)
            if path and os.path.isdir(path):
                layout.addWidget(self._make_quick_button(label, path))

        self._drive_layout = QHBoxLayout()
        self._drive_layout.setContentsMargins(0, 0, 0, 0)
        self._drive_layout.setSpacing(4)
        layout.addLayout(self._drive_layout)
        self._rebuild_drive_buttons()

        layout.addStretch()
        return layout

    def _rebuild_drive_buttons(self):
        """ドライブのボタンを作り直す（USB メモリなどを後から挿しても、PC 一覧を開くと反映される）"""
        while self._drive_layout.count():
            widget = self._drive_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for info in QDir.drives():  # ドライブを1つずつ調べないので、切断済みのネットワークドライブでも固まらない
            root = info.absoluteFilePath()
            self._drive_layout.addWidget(self._make_quick_button(f"💽 {root.rstrip('/') or root}", root))

    def _build_nav_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        self.back_button = QPushButton("⬅ 戻る")
        self.back_button.setToolTip("戻る (Alt+←)")
        self.back_button.clicked.connect(self.go_back)
        layout.addWidget(self.back_button)

        self.forward_button = QPushButton("➡ 進む")
        self.forward_button.setToolTip("進む (Alt+→)")
        self.forward_button.clicked.connect(self.go_forward)
        layout.addWidget(self.forward_button)

        self.path_input = QLineEdit()
        self.path_input.returnPressed.connect(self.on_path_entered)
        layout.addWidget(self.path_input)

        up_button = QPushButton("⬆ 上へ")
        up_button.clicked.connect(self.go_parent_directory)
        layout.addWidget(up_button)

        new_button = QPushButton("➕ 新規作成")
        new_menu = QMenu(new_button)
        folder_action = new_menu.addAction("📁 新しいフォルダ (Ctrl+Shift+N)")
        folder_action.triggered.connect(self.create_new_folder)
        text_action = new_menu.addAction("📄 新しいテキストファイル")
        text_action.triggered.connect(self.create_new_text_file)
        new_button.setMenu(new_menu)
        layout.addWidget(new_button)
        return layout

    def _update_nav_buttons(self):
        tab = self.current_tab()
        self.back_button.setEnabled(bool(tab and tab.can_go_back))
        self.forward_button.setEnabled(bool(tab and tab.can_go_forward))

    def _build_search_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 2)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 ファイル名で絞り込み (Ctrl+F)...")
        self.search_input.setStyleSheet(SEARCH_INPUT_STYLE)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        layout.addWidget(self.search_input)
        return layout

    # ==================================================================
    # 状態の取得・小さなヘルパー
    # ==================================================================
    def current_tab(self) -> "FileTab | None":
        widget = self.tab_widget.currentWidget()
        return widget if isinstance(widget, FileTab) else None

    def get_current_path(self) -> str:
        """現在のタブのフォルダ（PC のドライブ一覧のときは空文字）"""
        tab = self.current_tab()
        return tab.current_path if tab else ""

    def set_opposite_pane(self, pane: "PaneWidget"):
        """F5 / F6 の宛先になる反対側のペインを設定する（MainWindow が呼ぶ）"""
        self.opposite_pane = pane

    def set_pane_id(self, pane_id: str):
        """QSettings の保存先を分ける ID を設定する（MainWindow が呼ぶ）"""
        self.pane_id = pane_id

    @staticmethod
    def _settings() -> QSettings:
        return QSettings("KotaOkano", "NextGenFiler")

    def _settings_prefix(self) -> str:
        return f"pane/{self.pane_id}"

    def set_active_indicator(self, active: bool):
        """アクティブペインを枠線で示す (テーマに依らず分かるよう青い枠を使う)。"""
        color = "#007acc" if active else "transparent"
        self.setStyleSheet(f"PaneWidget {{ border: 2px solid {color}; }}")

    def get_opposite_pane_path(self) -> str:
        return self.opposite_pane.get_current_path() if self.opposite_pane else ""

    def set_status(self, text: str):
        self.status_label.setText(text)

    def _warn(self, text: str):
        QMessageBox.warning(self, "警告", text)

    def _in_archive_mode(self) -> bool:
        tab = self.current_tab()
        return bool(tab and tab.in_archive)

    def _block_in_archive(self) -> bool:
        """アーカイブ表示中はファイル操作を受け付けない (読み取り専用)。"""
        if self._in_archive_mode():
            self.set_status("アーカイブ内ではこの操作はできません（読み取り専用です）")
            return True
        return False

    @staticmethod
    def _archive_exit_path(archive: str) -> str:
        """アーカイブを抜けたときの移動先 (ZIP のあるフォルダ。無ければ PC 一覧)。"""
        parent = os.path.dirname(archive)
        return parent if parent and parent != archive else ""

    def _set_search_text(self, text: str):
        self.search_input.blockSignals(True)  # 絞り込みを再実行させない
        self.search_input.setText(text)
        self.search_input.blockSignals(False)

    def update_path_bar(self, path: str):
        parsed = split_archive_path(path)
        if parsed is not None:
            archive, inner = parsed
            self.path_input.setText(native(archive) + "!" + ("/" + inner if inner else ""))
        else:
            self.path_input.setText(native(path) if path else PC_LABEL)

    # ==================================================================
    # 設定の保存 / 復元 (QSettings)
    # ==================================================================
    def save_state(self):
        """タブ構成と表示設定を保存する (アプリ終了時に MainWindow が呼ぶ)。"""
        if not self.pane_id:
            return
        settings = self._settings()
        prefix = self._settings_prefix()
        paths = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, FileTab):
                paths.append(tab.current_path)
        settings.setValue(f"{prefix}/tabs", paths)
        settings.setValue(f"{prefix}/current_tab", self.tab_widget.currentIndex())
        settings.setValue(f"{prefix}/show_hidden", self.show_hidden)
        if self._view_widths:
            settings.setValue(f"{prefix}/column_widths", self._view_widths)
        if self._view_sort:
            column, order = self._view_sort
            settings.setValue(f"{prefix}/sort_column", column)
            settings.setValue(f"{prefix}/sort_order", int(order))

    def restore_state(self):
        """保存されたタブ構成と表示設定を復元する (起動時に MainWindow が呼ぶ)。"""
        if not self.pane_id:
            return
        settings = self._settings()
        prefix = self._settings_prefix()
        self.show_hidden = bool(settings.value(f"{prefix}/show_hidden", False, type=bool))
        widths = settings.value(f"{prefix}/column_widths", [])
        if widths:
            self._view_widths = [int(w) for w in widths]
        column = settings.value(f"{prefix}/sort_column", None)
        if column is not None:
            order = Qt.SortOrder(settings.value(f"{prefix}/sort_order", 0, type=int))
            self._view_sort = (int(column), order)

        paths = settings.value(f"{prefix}/tabs", [])
        if isinstance(paths, str):
            paths = [paths]
        # 起動時に作られた既定のタブを取り除いてから復元する
        while self.tab_widget.count():
            widget = self.tab_widget.widget(0)
            self.tab_widget.removeTab(0)
            if widget is not None:
                widget.deleteLater()
        if paths:
            for saved in paths:
                self.add_new_tab(self._restorable_path(saved))
            current = settings.value(f"{prefix}/current_tab", 0, type=int)
            if 0 <= current < self.tab_widget.count():
                self.tab_widget.setCurrentIndex(current)
        else:
            self.add_new_tab("")
        self._on_tab_changed(self.tab_widget.currentIndex())

    @staticmethod
    def _restorable_path(saved) -> str:
        """復元できるパスを返す。アーカイブの仮想パスは ZIP のあるフォルダに読み替える。"""
        if not saved:
            return ""
        if os.path.isdir(saved):
            return saved
        parsed = split_archive_path(saved)
        if parsed is not None:
            parent = os.path.dirname(parsed[0])
            if parent and os.path.isdir(parent):
                return parent
        return ""

    def _on_header_changed(self, *_args):
        """列幅やソートが変わったら、全タブに揃えて設定として保存する。"""
        if self._applying_view_settings:
            return
        tab = self.current_tab()
        if tab is None:
            return
        self._view_widths = tab.column_widths()
        self._view_sort = tab.sort_state()
        self._apply_view_settings(except_tab=tab)
        if self.pane_id:
            settings = self._settings()
            prefix = self._settings_prefix()
            settings.setValue(f"{prefix}/column_widths", self._view_widths)
            column, order = self._view_sort
            settings.setValue(f"{prefix}/sort_column", column)
            settings.setValue(f"{prefix}/sort_order", int(order))

    def _apply_view_settings(self, except_tab=None):
        """保持している列幅・ソート・隠しファイル設定を各タブへ適用する。"""
        self._applying_view_settings = True
        try:
            for i in range(self.tab_widget.count()):
                tab = self.tab_widget.widget(i)
                if not isinstance(tab, FileTab):
                    continue
                tab.set_show_hidden(self.show_hidden)
                if tab is except_tab:
                    continue
                if self._view_widths:
                    tab.apply_column_widths(self._view_widths)
                if self._view_sort:
                    tab.apply_sort(*self._view_sort)
        finally:
            self._applying_view_settings = False

    def toggle_hidden_files(self):
        """隠しファイルの表示 / 非表示を切り替える (Ctrl+H)。"""
        self.show_hidden = not self.show_hidden
        self._apply_view_settings()
        self.set_status("隠しファイルを" + ("表示します" if self.show_hidden else "表示しません"))
        if self.pane_id:
            self._settings().setValue(f"{self._settings_prefix()}/show_hidden", self.show_hidden)

    # ==================================================================
    # タブ
    # ==================================================================
    def add_new_tab(self, path: str = ""):
        path = normalize_path(path)
        if path and not os.path.isdir(path) and split_archive_path(path) is None:
            path = ""

        tab = FileTab(self, path, self.tab_widget)
        tab.selection_changed.connect(self.update_selection_stats)
        tab.view.doubleClicked.connect(self._on_item_double_clicked)
        header = tab.view.horizontalHeader()
        header.sectionResized.connect(self._on_header_changed)
        header.sortIndicatorChanged.connect(self._on_header_changed)
        self._apply_view_settings()  # 列幅・ソート・隠しファイル設定を引き継ぐ

        index = self.tab_widget.addTab(tab, tab_title(path))
        self.tab_widget.setCurrentIndex(index)

    def open_new_tab_current_path(self):
        """Ctrl+T: 現在のタブと同じフォルダを引き継いで新しいタブを開く"""
        self.add_new_tab(self.get_current_path())

    def close_tab(self, index: int):
        if self.tab_widget.count() <= 1:
            return
        widget = self.tab_widget.widget(index)
        self.tab_widget.removeTab(index)
        if widget is not None:
            widget.deleteLater()  # モデルごと解放する

    def close_current_tab(self):
        """Ctrl+W: 現在のタブを閉じる (最後の1つは閉じない)。"""
        self.close_tab(self.tab_widget.currentIndex())

    def activate_next_tab(self):
        """Ctrl+Tab: 次のタブへ切り替える。"""
        count = self.tab_widget.count()
        if count > 1:
            self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() + 1) % count)

    def activate_previous_tab(self):
        """Ctrl+Shift+Tab: 前のタブへ切り替える。"""
        count = self.tab_widget.count()
        if count > 1:
            self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() - 1) % count)

    def _on_tab_changed(self, _index: int):
        tab = self.current_tab()
        if tab is None:
            return
        self._update_nav_buttons()
        self.update_path_bar(tab.current_path)
        self._set_search_text(tab.filter_text)
        self.update_selection_stats()
        self.path_changed.emit(tab.current_path)

    # ==================================================================
    # ナビゲーション
    # ==================================================================
    def navigate_to_path(self, path: str):
        tab = self.current_tab()
        if tab is None:
            return

        path = normalize_path(path)
        if path and not os.path.isdir(path):
            self._warn(f"フォルダが見つかりません:\n{native(path)}")
            return

        if not path:
            self._rebuild_drive_buttons()

        tab.navigate(path)
        self._sync_after_navigation(tab)

    def _sync_after_navigation(self, tab: "FileTab"):
        path = tab.current_path
        if not path:
            self._rebuild_drive_buttons()
        tab.set_filter("")
        self._set_search_text("")
        self.update_path_bar(path)
        self.tab_widget.setTabText(self.tab_widget.currentIndex(), tab_title(path))
        self.update_selection_stats()
        self._update_nav_buttons()
        self.path_changed.emit(path)
        if tab.in_archive:
            self.set_status(f"📦 アーカイブ内を表示しています（読み取り専用）: {os.path.basename(tab.archive_path)}")

    def go_back(self):
        tab = self.current_tab()
        if tab is not None and tab.go_back() is not None:
            self._sync_after_navigation(tab)

    def go_forward(self):
        tab = self.current_tab()
        if tab is not None and tab.go_forward() is not None:
            self._sync_after_navigation(tab)

    # ==================================================================
    # 新規作成
    # ==================================================================
    def create_new_folder(self):
        self._create_new_item("folder")

    def create_new_text_file(self):
        self._create_new_item("text")

    def _create_new_item(self, kind: str):
        if self._block_in_archive():
            return
        tab = self.current_tab()
        dest_dir = self.get_current_path()
        if tab is None or not dest_dir or not os.path.isdir(dest_dir):
            self._warn("項目を作成できるフォルダを開いてください。（PCドライブ一覧では作成できません）")
            return
        try:
            if kind == "folder":
                path = file_ops.create_folder(dest_dir)
            else:
                path = file_ops.create_text_file(dest_dir)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"作成できませんでした:\n{exc}")
            return
        self.set_status(f"作成しました: {os.path.basename(path)}")
        tab.select_and_rename(path)

    def on_path_entered(self):
        path = parse_user_path(self.path_input.text())
        if split_archive_path(path) is not None:
            tab = self.current_tab()
            if tab is not None:
                tab.navigate(path)
                self._sync_after_navigation(tab)
            return
        self.navigate_to_path(path)

    def go_parent_directory(self):
        tab = self.current_tab()
        if tab is not None and tab.in_archive:
            if tab.archive_inner:
                parent_inner = tab.archive_inner.rpartition("/")[0]
                tab.navigate(archive_virtual_path(tab.archive_path, parent_inner))
            else:
                tab.navigate(self._archive_exit_path(tab.archive_path))
            self._sync_after_navigation(tab)
            return
        path = self.get_current_path()
        if not path:
            return
        parent = os.path.dirname(path)
        # ドライブ直下の「上へ」は PC のドライブ一覧に戻る
        self.navigate_to_path("" if not parent or parent == path else parent)

    def on_search_text_changed(self, text: str):
        """検索ボックスの文字でファイル一覧をリアルタイムに絞り込む"""
        tab = self.current_tab()
        if tab:
            tab.set_filter(text)

    def focus_search_bar(self):
        self.search_input.setFocus()
        self.search_input.selectAll()

    def focus_path_bar(self):
        self.path_input.setFocus()
        self.path_input.selectAll()

    # ==================================================================
    # 開く・プレビュー・リネーム
    # ==================================================================
    def _on_item_double_clicked(self, index: QModelIndex):
        tab = self.current_tab()
        if tab:
            self._open_path(tab.path_at(index))

    def open_selected_item(self):
        tab = self.current_tab()
        if tab:
            self._open_path(tab.current_item_path())

    def _open_path(self, path: str):
        if not path:
            return
        tab = self.current_tab()
        if tab is not None and tab.in_archive:
            parsed = split_archive_path(path)
            if parsed is not None:
                archive, inner = parsed
                if tab.archive_entry_is_dir(inner):
                    tab.navigate(path)
                    self._sync_after_navigation(tab)
                elif inner:
                    self._open_archive_entry(archive, inner)
                return
        if os.path.isdir(path):
            self.navigate_to_path(path)
        elif os.path.isfile(path):
            if path.lower().endswith(".zip"):
                self._open_archive(path)
            else:
                self._open_file(path)

    def _open_archive(self, archive: str):
        """ZIP を開き、同じタブをアーカイブ表示に切り替える。"""
        tab = self.current_tab()
        if tab is None:
            return
        try:
            file_ops.list_zip_entries(archive, "")
        except Exception as exc:  # noqa: BLE001
            self._warn(f"ZIP を開けませんでした:\n{exc}")
            return
        tab.navigate(archive_virtual_path(archive))
        self._sync_after_navigation(tab)

    def _open_archive_entry(self, archive: str, inner: str):
        """アーカイブ内のファイルを1つだけ展開して、関連付けられたアプリで開く。"""
        try:
            extracted = file_ops.extract_zip_entry(archive, inner)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"ファイルを開けませんでした:\n{exc}")
            return
        self._open_file(extracted)

    def leave_archive(self):
        """アーカイブ表示を終了し、ZIP のあるフォルダへ戻る。"""
        tab = self.current_tab()
        if tab is None or not tab.in_archive:
            return
        tab.navigate(self._archive_exit_path(tab.archive_path))
        self._sync_after_navigation(tab)

    def _open_file(self, path: str):
        try:
            if hasattr(os, "startfile"):  # Windows: 関連付けられたアプリで開く / 実行する
                os.startfile(native(path))
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                raise OSError("関連付けられたアプリが見つかりません")
        except Exception as exc:  # noqa: BLE001
            self._warn(f"ファイルを開けませんでした:\n{exc}")

    def open_in_explorer(self, path: str):
        if not path:
            return
        try:
            if sys.platform == "win32":
                if os.path.isfile(path):
                    # エクスプローラーは成功しても終了コード 1 を返すので、結果は確認しない
                    subprocess.Popen(["explorer", "/select,", native(path)])
                else:
                    os.startfile(native(path))
            else:
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except Exception as exc:  # noqa: BLE001
            self._warn(f"エクスプローラーを開けませんでした:\n{exc}")

    def open_quick_look(self):
        tab = self.current_tab()
        path = tab.current_item_path() if tab else ""
        if path and os.path.isfile(path):
            QuickLookDialog(path, self).exec()

    def open_batch_rename(self):
        if self._block_in_archive():
            return
        tab = self.current_tab()
        selected = tab.selected_paths() if tab else []
        if not selected:
            self._warn("リネームするファイルが選択されていません。")
            return

        file_paths = [p for p in selected if os.path.isfile(p)]
        if not file_paths:
            self._warn("ファイルのみ一括リネームが可能です。")
            return

        BatchRenameDialog(file_paths, self).exec()

    def open_hash_dialog(self):
        """選択中のファイル1つのハッシュ計算ダイアログを開く。"""
        if self._block_in_archive():
            return
        tab = self.current_tab()
        selected = tab.selected_paths() if tab else []
        if len(selected) != 1 or not os.path.isfile(selected[0]):
            self._warn("ハッシュを計算するファイルを1つ選択してください。")
            return
        HashDialog(selected[0], self).exec()

    # ==================================================================
    # 選択の統計
    # ==================================================================
    def update_selection_stats(self):
        tab = self.current_tab()
        if tab is None:
            return

        files, dirs, total = tab.selection_summary()
        if files == 0 and dirs == 0:
            self.set_status(STATUS_DEFAULT)
            return

        parts = []
        if files:
            parts.append(f"ファイル {files}個")
        if dirs:
            parts.append(f"フォルダ {dirs}個")
        self.set_status(f"選択中: {' / '.join(parts)}  |  合計サイズ: {file_ops.format_size(total)}")

        # フォルダが1つだけ選ばれているときは、中身の合計サイズも数えて表示する
        selected = tab.selected_paths()
        if dirs == 1 and files == 0 and len(selected) == 1 and not tab.in_archive:
            self.set_status("選択中: フォルダ 1個  |  合計サイズ: 計算中...")
            start_folder_size_worker(selected[0], self._on_folder_size_ready)

    def _on_folder_size_ready(self, path: str, size: int):
        tab = self.current_tab()
        if tab is None or tab.selected_paths() != [path]:
            return  # 選択が変わっていたら古い結果は捨てる
        self.set_status(f"選択中: フォルダ 1個  |  合計サイズ: {file_ops.format_size(size)}")

    # ==================================================================
    # クリップボード（システムのクリップボードを唯一の情報源にする）
    # ==================================================================
    def copy_to_clipboard(self):
        self._set_clipboard(cut=False)

    def cut_to_clipboard(self):
        self._set_clipboard(cut=True)

    def _set_clipboard(self, cut: bool):
        if self._block_in_archive():
            return
        tab = self.current_tab()
        paths = tab.selected_paths() if tab else []
        verb = "切り取る" if cut else "コピーする"
        if not paths:
            self._warn(f"{verb}項目が選択されていません。")
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        effect = DROP_EFFECT_MOVE if cut else DROP_EFFECT_COPY
        mime.setData(DROP_EFFECT_FORMAT, QByteArray(struct.pack("<I", effect)))
        QGuiApplication.clipboard().setMimeData(mime)

        if cut:
            self.set_status(f"{len(paths)} 件の項目を切り取りました（貼り付け待ち）")
        else:
            self.set_status(f"{len(paths)} 件の項目をコピーしました（クリップボードに保持）")

    @staticmethod
    def _read_clipboard() -> tuple:
        """(パスのリスト, 切り取りか) を返す。エクスプローラーでコピー / 切り取りしたものも読める。"""
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None or not mime.hasUrls():
            return [], False

        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        is_cut = False
        if mime.hasFormat(DROP_EFFECT_FORMAT):
            data = bytes(mime.data(DROP_EFFECT_FORMAT))
            if len(data) >= 4:
                is_cut = bool(struct.unpack("<I", data[:4])[0] & DROP_EFFECT_MOVE)
        return paths, is_cut

    def paste_items(self):
        if self._block_in_archive():
            return
        dest_dir = self.get_current_path()
        if not dest_dir or not os.path.isdir(dest_dir):
            self._warn("貼り付け先のディレクトリが有効ではありません。（PCドライブ一覧では貼り付けできません）")
            return

        paths, is_cut = self._read_clipboard()
        if not paths:
            self._warn("クリップボードに貼り付けられるファイルがありません。")
            return

        self._run_operation("move" if is_cut else "copy", paths, dest_dir, clear_clipboard=is_cut)

    # ==================================================================
    # ドラッグ＆ドロップの受け入れ
    # ==================================================================
    def accept_drop(self, paths: list, dest_dir: str, move: bool):
        """ドロップされた項目を dest_dir へコピー / 移動する (ペイン間・エクスプローラー間共通)。"""
        if self._in_archive_mode():
            self.set_status("アーカイブ内へはドロップできません")
            return
        if not dest_dir or not os.path.isdir(dest_dir):
            return
        dest_abs = os.path.normcase(os.path.abspath(dest_dir))
        targets = []
        for p in paths:
            if not p or not os.path.lexists(p):
                continue
            if os.path.normcase(os.path.dirname(os.path.abspath(p))) == dest_abs:
                continue  # 同じフォルダへのドロップは何もしない
            if os.path.isdir(p) and file_ops._is_inside(dest_dir, p):
                continue  # フォルダを自分自身の中へは入れられない
            targets.append(p)
        if not targets:
            self.set_status("ドロップできる項目がありませんでした")
            return
        self._run_operation("move" if move else "copy", targets, dest_dir)

    # ==================================================================
    # パスをコピー / プロパティ / ZIP
    # ==================================================================
    def copy_path_to_clipboard(self, path: str):
        QGuiApplication.clipboard().setText(native(path))
        self.set_status(f"パスをコピーしました: {native(path)}")

    def show_properties(self, path: str):
        if split_archive_path(path) is not None:
            self.set_status("アーカイブ内の項目のプロパティは表示できません")
            return
        if path:
            PropertiesDialog(path, self).exec()

    def compress_selected_to_zip(self):
        """選択中の項目を、表示中のフォルダ内に ZIP 圧縮する。"""
        if self._block_in_archive():
            return
        tab = self.current_tab()
        paths = tab.selected_paths() if tab else []
        if not paths:
            self._warn("圧縮する項目が選択されていません。")
            return
        dest_dir = self.get_current_path()
        if not dest_dir or not os.path.isdir(dest_dir):
            self._warn("圧縮先のフォルダが有効ではありません。（PCドライブ一覧では圧縮できません）")
            return
        self._run_archive("compress", paths, dest_dir)

    def extract_archive_to_parent(self, archive: str):
        """アーカイブ表示中の ZIP を、ZIP のあるフォルダへすべて展開する。"""
        dest_dir = os.path.dirname(archive)
        if not dest_dir or not os.path.isdir(dest_dir):
            self._warn("展開先のフォルダが有効ではありません。")
            return
        self._run_archive("extract", [], dest_dir, archive=archive)

    def extract_zip_archive(self, archive: str):
        """ZIP を表示中のフォルダへ展開する。"""
        dest_dir = self.get_current_path()
        if not dest_dir or not os.path.isdir(dest_dir):
            self._warn("展開先のフォルダが有効ではありません。（PCドライブ一覧では展開できません）")
            return
        self._run_archive("extract", [], dest_dir, archive=archive)

    def _run_archive(self, kind: str, paths: list, dest_dir: str, archive: str = ""):
        if self._archive_worker is not None:
            self.set_status("別の ZIP 処理が実行中です。完了までお待ちください。")
            return
        if self._worker is not None:
            self.set_status("別のファイル操作が実行中です。完了までお待ちください。")
            return

        label = "圧縮" if kind == "compress" else "展開"
        self._archive_kind = kind
        progress = QProgressDialog(f"{label}を準備中...", "キャンセル", 0, 100, self)
        progress.setWindowTitle(f"ZIP{label}")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(400)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        worker = ArchiveWorker(kind, paths, dest_dir, archive, self)
        worker.progressed.connect(progress.setValue)
        worker.done.connect(self._on_archive_finished)
        progress.canceled.connect(worker.cancel)

        self._archive_worker, self._archive_progress = worker, progress
        worker.start()

    def _on_archive_finished(self, outcome):
        ok, payload = outcome
        worker, progress = self._archive_worker, self._archive_progress
        self._archive_worker = self._archive_progress = None

        if progress is not None:
            progress.reset()
            progress.hide()
            progress.deleteLater()
        if worker is not None:
            worker.wait()
            worker.deleteLater()

        label = "圧縮" if self._archive_kind == "compress" else "展開"
        if ok:
            if self._archive_kind == "compress":
                self.set_status(f"圧縮しました: {os.path.basename(payload)}")
            else:
                self.set_status(f"展開しました: {len(payload)} 件の項目")
        else:
            self._warn(f"ZIP の{label}に失敗しました:\n{payload}")

    # ==================================================================
    # 反対ペインへのコピー / 移動、削除
    # ==================================================================
    def copy_items(self):
        """F5: 選択項目を反対ペインのフォルダへコピーする"""
        self._transfer_to_opposite("copy")

    def move_items(self):
        """F6: 選択項目を反対ペインのフォルダへ移動する"""
        self._transfer_to_opposite("move")

    def _transfer_to_opposite(self, kind: str):
        if self._block_in_archive():
            return
        tab = self.current_tab()
        paths = tab.selected_paths() if tab else []
        verb = OPERATION_LABELS[kind]
        if not paths:
            self._warn(f"{verb}する項目が選択されていません。")
            return

        dest_dir = self.get_opposite_pane_path()
        if not dest_dir or not os.path.isdir(dest_dir):
            self._warn(f"{verb}先のディレクトリ（反対側のペイン）が有効ではありません。")
            return

        self._run_operation(kind, paths, dest_dir)

    def delete_items(self):
        """Delete / F8: ごみ箱へ移動する（send2trash がない場合は完全削除）"""
        self._delete(permanent=False)

    def delete_permanently(self):
        """Shift+Delete: ごみ箱を経由せず完全に削除する"""
        self._delete(permanent=True)

    def _delete(self, permanent: bool):
        if self._block_in_archive():
            return
        tab = self.current_tab()
        paths = tab.selected_paths() if tab else []
        if not paths:
            self._warn("削除する項目が選択されていません。")
            return

        to_trash = file_ops.TRASH_AVAILABLE and not permanent
        listing = self._describe_paths(paths)
        if to_trash:
            text = f"選択した {len(paths)} 件の項目をごみ箱に移動しますか？\n\n{listing}"
        else:
            text = f"選択した {len(paths)} 件の項目を完全に削除しますか？\n\n{listing}\n\n（この操作は元に戻せません）"
            if not permanent:
                text += "\n※ send2trash が未インストールのため、ごみ箱は使えません。"

        reply = QMessageBox.question(
            self, "削除の確認", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._run_operation("trash" if to_trash else "delete", paths)

    @staticmethod
    def _describe_paths(paths: list, limit: int = 5) -> str:
        names = [os.path.basename(os.path.normpath(p)) or p for p in paths[:limit]]
        text = "\n".join(f"  ・{name}" for name in names)
        if len(paths) > limit:
            text += f"\n  …ほか {len(paths) - limit} 件"
        return text

    # ==================================================================
    # バックグラウンド実行
    # ==================================================================
    def _ask_conflict_policy(self, paths: list, dest_dir: str):
        """宛先に同名の項目がある場合に、どう扱うかをユーザーに選んでもらう。

        衝突が無ければ既定の CONFLICT_RENAME、キャンセルされたら None を返す。
        """
        conflicts = [
            p
            for p in paths
            if os.path.lexists(os.path.join(dest_dir, os.path.basename(os.path.normpath(p))))
        ]
        if not conflicts:
            return file_ops.CONFLICT_RENAME
        has_folder_conflict = any(
            os.path.isdir(p)
            and os.path.isdir(os.path.join(dest_dir, os.path.basename(os.path.normpath(p))))
            for p in conflicts
        )
        dialog = ConflictDialog(conflicts, dest_dir, has_folder_conflict, self)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.policy()

    def _run_operation(self, kind: str, paths: list, dest_dir: str = "", clear_clipboard: bool = False):
        if self._worker is not None:
            self.set_status("別のファイル操作が実行中です。完了までお待ちください。")
            return
        if self._archive_worker is not None:
            self.set_status("別の ZIP 処理が実行中です。完了までお待ちください。")
            return

        label = OPERATION_LABELS[kind]

        on_conflict = file_ops.CONFLICT_RENAME
        if kind in ("copy", "move") and dest_dir:
            on_conflict = self._ask_conflict_policy(paths, dest_dir)
            if on_conflict is None:
                self.set_status(f"{label}をキャンセルしました")
                return

        self._operation = (kind, dest_dir, clear_clipboard)

        progress = QProgressDialog(f"{label}を準備中...", "キャンセル", 0, 100, self)
        progress.setWindowTitle(label)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(400)  # 一瞬で終わる操作ではダイアログを出さない
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        worker = FileOperationWorker(kind, paths, dest_dir, self, on_conflict=on_conflict)
        worker.progressed.connect(self._on_operation_progress)
        worker.overall.connect(progress.setValue)
        worker.done.connect(self._on_operation_finished)
        progress.canceled.connect(worker.cancel)

        self._worker, self._progress = worker, progress
        worker.start()

    def _on_operation_progress(self, index: int, total: int, name: str):
        progress = self._progress
        if progress is None or progress.wasCanceled():
            return
        label = OPERATION_LABELS[self._operation[0]]
        progress.setLabelText(f"{label}中 ({index + 1}/{total}):\n{name}")

    def _on_operation_finished(self, result):
        kind, dest_dir, clear_clipboard = self._operation
        worker, progress = self._worker, self._progress
        self._worker = self._progress = None

        if progress is not None:
            progress.reset()
            progress.hide()
            progress.deleteLater()
        if worker is not None:
            worker.wait()
            worker.deleteLater()

        if clear_clipboard and result.success_count > 0:
            QGuiApplication.clipboard().clear()  # 切り取りは1回貼り付けたら終わり

        label = OPERATION_LABELS[kind]
        parts = [f"{result.success_count} 件を{label}しました"]
        skipped = getattr(result, "skipped", [])
        if skipped:
            parts.append(f"スキップ {len(skipped)} 件")
        if result.failed:
            parts.append(f"失敗 {len(result.failed)} 件")
        if result.cancelled:
            parts.append("キャンセルされました")
        if dest_dir and result.success_count:
            parts.append(f"宛先: {native(dest_dir)}")
        self.set_status("  |  ".join(parts))

        if result.failed:
            box = QMessageBox(
                QMessageBox.Warning,
                f"{label}できなかった項目があります",
                f"{len(result.failed)} 件の項目を{label}できませんでした。\n詳細を表示すると理由を確認できます。",
                QMessageBox.Ok,
                self,
            )
            box.setDetailedText("\n".join(f"{native(path)}\n    → {reason}" for path, reason in result.failed))
            box.exec()

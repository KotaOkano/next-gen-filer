
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys

from PySide6.QtCore import (
    QByteArray,
    QDir,
    QMimeData,
    QModelIndex,
    QSortFilterProxyModel,
    QStandardPaths,
    Qt,
    QThread,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
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
from ui.batch_rename_dialog import BatchRenameDialog
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


def tab_title(path: str) -> str:
    if not path:
        return "PC"
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


# ----------------------------------------------------------------------
# テーブルビュー（キー操作・右クリックメニュー）
# ----------------------------------------------------------------------
class InteractiveTableView(QTableView):
    """キー操作と右クリックメニューを処理するテーブルビュー"""

    def __init__(self, pane: "PaneWidget", parent=None):
        super().__init__(parent)
        self.pane = pane

        # ダブルクリック等による勝手な編集を防ぐ（名前変更は F2 / メニューから明示的に開始する）
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

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
            (Qt.Key_Backspace, Qt.NoModifier, pane.go_parent_directory),
            (Qt.Key_Up, Qt.AltModifier, pane.go_parent_directory),
        ]

    # ---- キー操作 ----------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
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
    def _path_of(self, index: QModelIndex) -> str:
        proxy = self.model()
        return proxy.sourceModel().filePath(proxy.mapToSource(index))

    def rename_index(self, index: QModelIndex):
        if not index.isValid():
            return
        index = index.siblingAtColumn(0)  # 名前の列だけが編集できる
        path = self._path_of(index)
        if not path or file_ops.is_root(path):  # ドライブ名は変更しない
            return
        self.edit(index)

    def rename_current(self):
        self.rename_index(self.currentIndex())

    # ---- 右クリックメニュー ------------------------------------------
    def show_context_menu(self, pos):
        pane = self.pane
        index = self.indexAt(pos)
        menu = QMenu(self)

        def add(text, slot):
            action = QAction(text, menu)
            action.triggered.connect(lambda _checked=False: slot())
            menu.addAction(action)

        if not index.isValid():
            add("貼り付け (Ctrl+V)", pane.paste_items)
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

        self.model = QFileSystemModel(self)
        self.model.setReadOnly(False)  # 名前の変更を許可する（編集できるのは名前の列だけ）

        self.proxy = FileSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)

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
        self.model.setRootPath(path)
        if path:
            self.view.setRootIndex(self.proxy.mapFromSource(self.model.index(path)))
        else:
            self.view.setRootIndex(QModelIndex())
        self._path = path

    def set_filter(self, text: str):
        self.filter_text = text
        self.proxy.setFilterFixedString(text)

    # ---- 選択・パスの取得 --------------------------------------------
    def path_at(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
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
    done = Signal(object)  # file_ops.OperationResult

    def __init__(self, kind: str, paths: list, dest_dir: str = "", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.paths = list(paths)
        self.dest_dir = dest_dir
        self._cancelled = False

    def cancel(self):
        """次の項目に進む前に中断する（処理中の1件は最後まで実行される）"""
        self._cancelled = True

    def run(self):
        actions = {
            "copy": lambda p: file_ops.copy_item(p, self.dest_dir),
            "move": lambda p: file_ops.move_item(p, self.dest_dir),
            "trash": lambda p: file_ops.delete_item(p, permanent=False),
            "delete": lambda p: file_ops.delete_item(p, permanent=True),
        }
        result = file_ops.run_batch(
            self.paths,
            actions[self.kind],
            progress=self.progressed.emit,
            is_cancelled=lambda: self._cancelled,
        )
        self.done.emit(result)


# ----------------------------------------------------------------------
# ペイン本体
# ----------------------------------------------------------------------
class PaneWidget(QWidget):
    """左右独立して動作するデュアルペインの片側"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.opposite_pane: "PaneWidget | None" = None
        self._worker: "FileOperationWorker | None" = None
        self._progress: "QProgressDialog | None" = None
        self._operation = ("", "", False)  # (種類, 宛先, 終了後にクリップボードを空にするか)
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
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tab_widget)

        self.status_label = QLabel(STATUS_DEFAULT)
        self.status_label.setStyleSheet(STATUS_STYLE)
        layout.addWidget(self.status_label)

        self.add_new_tab("")

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

        self.path_input = QLineEdit()
        self.path_input.returnPressed.connect(self.on_path_entered)
        layout.addWidget(self.path_input)

        up_button = QPushButton("⬆ 上へ")
        up_button.clicked.connect(self.go_parent_directory)
        layout.addWidget(up_button)
        return layout

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

    def get_opposite_pane_path(self) -> str:
        return self.opposite_pane.get_current_path() if self.opposite_pane else ""

    def set_status(self, text: str):
        self.status_label.setText(text)

    def _warn(self, text: str):
        QMessageBox.warning(self, "警告", text)

    def _set_search_text(self, text: str):
        self.search_input.blockSignals(True)  # 絞り込みを再実行させない
        self.search_input.setText(text)
        self.search_input.blockSignals(False)

    def update_path_bar(self, path: str):
        self.path_input.setText(native(path) if path else PC_LABEL)

    # ==================================================================
    # タブ
    # ==================================================================
    def add_new_tab(self, path: str = ""):
        path = normalize_path(path)
        if path and not os.path.isdir(path):
            path = ""

        tab = FileTab(self, path, self.tab_widget)
        tab.selection_changed.connect(self.update_selection_stats)
        tab.view.doubleClicked.connect(self._on_item_double_clicked)

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

    def _on_tab_changed(self, _index: int):
        tab = self.current_tab()
        if tab is None:
            return
        self.update_path_bar(tab.current_path)
        self._set_search_text(tab.filter_text)
        self.update_selection_stats()

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
        tab.set_filter("")
        self._set_search_text("")
        self.update_path_bar(path)
        self.tab_widget.setTabText(self.tab_widget.currentIndex(), tab_title(path))
        self.update_selection_stats()

    def on_path_entered(self):
        self.navigate_to_path(parse_user_path(self.path_input.text()))

    def go_parent_directory(self):
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
        if os.path.isdir(path):
            self.navigate_to_path(path)
        elif os.path.isfile(path):
            self._open_file(path)

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

    # ==================================================================
    # クリップボード（システムのクリップボードを唯一の情報源にする）
    # ==================================================================
    def copy_to_clipboard(self):
        self._set_clipboard(cut=False)

    def cut_to_clipboard(self):
        self._set_clipboard(cut=True)

    def _set_clipboard(self, cut: bool):
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
    # 反対ペインへのコピー / 移動、削除
    # ==================================================================
    def copy_items(self):
        """F5: 選択項目を反対ペインのフォルダへコピーする"""
        self._transfer_to_opposite("copy")

    def move_items(self):
        """F6: 選択項目を反対ペインのフォルダへ移動する"""
        self._transfer_to_opposite("move")

    def _transfer_to_opposite(self, kind: str):
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
    def _run_operation(self, kind: str, paths: list, dest_dir: str = "", clear_clipboard: bool = False):
        if self._worker is not None:
            self.set_status("別のファイル操作が実行中です。完了までお待ちください。")
            return

        label = OPERATION_LABELS[kind]
        self._operation = (kind, dest_dir, clear_clipboard)

        progress = QProgressDialog(f"{label}を準備中...", "キャンセル", 0, len(paths), self)
        progress.setWindowTitle(label)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(400)  # 一瞬で終わる操作ではダイアログを出さない
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        worker = FileOperationWorker(kind, paths, dest_dir, self)
        worker.progressed.connect(self._on_operation_progress)
        worker.done.connect(self._on_operation_finished)
        progress.canceled.connect(worker.cancel)

        self._worker, self._progress = worker, progress
        worker.start()

    def _on_operation_progress(self, index: int, total: int, name: str):
        progress = self._progress
        if progress is None or progress.wasCanceled():
            return
        label = OPERATION_LABELS[self._operation[0]]
        progress.setValue(index)
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

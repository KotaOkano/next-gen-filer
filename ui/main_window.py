from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from version import __version__
from ui.app_icon import create_app_icon
from ui.disk_analyzer import get_drive_space_info
from ui.file_tools_dialog import FileToolsDialog
from ui.pane_widget import PaneWidget
from ui.sftp_connect_dialog import SFTPConnectDialog
from ui.tree_sidebar import TreeSidebar

BUTTON_STYLE = "font-weight: bold; padding: 6px;"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Next-Gen File Manager v{__version__}")
        self.setWindowIcon(create_app_icon())
        self.resize(1200, 800)

        self.init_ui()
        self._restore_window_state()
        self.init_shortcuts()

    # ------------------------------------------------------------------
    # 設定の保存 / 復元 (QSettings)
    # ------------------------------------------------------------------
    @staticmethod
    def _settings() -> QSettings:
        return QSettings("KotaOkano", "NextGenFiler")

    def _restore_window_state(self):
        settings = self._settings()
        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter_state = settings.value("window/splitter")
        if splitter_state is not None:
            self.splitter.restoreState(splitter_state)

    def closeEvent(self, event):
        settings = self._settings()
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/splitter", self.splitter.saveState())
        self.left_pane.save_state()
        self.right_pane.save_state()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------
    def init_ui(self):
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        main_layout.addLayout(self._create_button_bar())
        main_layout.addWidget(self._create_splitter())

        self.setCentralWidget(central_widget)

    def _create_button_bar(self) -> QHBoxLayout:
        """上部のツールボタン群を作成する（左寄せ）"""
        layout = QHBoxLayout()

        self.analyzer_btn = self._create_button("💾 空き容量", self.show_disk_space_info)
        self.file_tools_btn = self._create_button("🔒 ファイル暗号化/完全削除", self.open_file_tools)
        self.sftp_btn = self._create_button(
            "🌐 SFTP接続",
            self.open_sftp_dialog,
            tooltip="リモートサーバーやNASへSFTP接続するダイアログを開きます",
        )

        for button in (self.analyzer_btn, self.file_tools_btn, self.sftp_btn):
            layout.addWidget(button)

        layout.addStretch()  # ボタンを左側に寄せる
        return layout

    @staticmethod
    def _create_button(text: str, slot, tooltip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setStyleSheet(BUTTON_STYLE)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(slot)
        return button

    def _create_splitter(self) -> QSplitter:
        """ツリー ＋ 左右ペインのスプリッターを作成する"""
        self.splitter = QSplitter(Qt.Horizontal)

        self.tree_sidebar = TreeSidebar()
        self.tree_sidebar.open_in_pane_signal.connect(self.handle_tree_navigation)

        self.left_pane = PaneWidget()
        self.right_pane = PaneWidget()
        # F5 / F6 の宛先として、お互いを教えておく
        self.left_pane.set_opposite_pane(self.right_pane)
        self.right_pane.set_opposite_pane(self.left_pane)
        # QSettings の保存先を分けるための ID
        self.left_pane.set_pane_id("left")
        self.right_pane.set_pane_id("right")
        # ペインの現在地が変わったらツリーの選択を連動させる
        self.left_pane.path_changed.connect(self._sync_tree_to_pane)
        self.right_pane.path_changed.connect(self._sync_tree_to_pane)

        self.splitter.addWidget(self.tree_sidebar)
        self.splitter.addWidget(self.left_pane)
        self.splitter.addWidget(self.right_pane)
        self.splitter.setSizes([250, 475, 475])

        # 最後にフォーカスのあったペインを覚えておく（ツリーやダイアログにフォーカスが移っても迷わない）
        self._active_pane = self.left_pane
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        # 前回のタブ構成・表示設定を復元する
        self.left_pane.restore_state()
        self.right_pane.restore_state()
        self.left_pane.set_active_indicator(True)
        self.right_pane.set_active_indicator(False)
        return self.splitter

    def init_shortcuts(self):
        """キーボードショートカットの登録"""
        self.shortcut_new_tab = QShortcut(QKeySequence("Ctrl+T"), self)
        self.shortcut_new_tab.activated.connect(self.on_new_tab_triggered)

        self.shortcut_search = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        self.shortcut_search.activated.connect(self.on_search_triggered)

    # ------------------------------------------------------------------
    # ペイン操作
    # ------------------------------------------------------------------
    def _on_focus_changed(self, _old, new):
        if new is None:
            return
        for pane in (self.left_pane, self.right_pane):
            if pane is new or pane.isAncestorOf(new):
                if self._active_pane is not pane:
                    self._active_pane = pane
                    self.left_pane.set_active_indicator(pane is self.left_pane)
                    self.right_pane.set_active_indicator(pane is self.right_pane)
                    path = pane.get_current_path()
                    if path:
                        self.tree_sidebar.select_path(path)
                return

    def _sync_tree_to_pane(self, path: str):
        """アクティブペインの現在地にツリーの選択を合わせる。"""
        if not path:
            return
        sender_pane = self.sender()
        if sender_pane is not None and sender_pane is not self._active_pane:
            return
        self.tree_sidebar.select_path(path)

    def get_active_pane(self) -> PaneWidget:
        """最後にフォーカスのあったペインを返す（最初は左ペイン）"""
        return self._active_pane

    def handle_tree_navigation(self, path: str, pane_side: str):
        """ツリーの選択に応じてペインでフォルダを開く。

        pane_side が "left" / "right" ならそのペイン、"active" (左クリック) なら
        アクティブペインが対象になる。
        """
        if pane_side == "left":
            target_pane = self.left_pane
        elif pane_side == "right":
            target_pane = self.right_pane
        else:
            target_pane = self.get_active_pane()
        target_pane.navigate_to_path(path)

    # ------------------------------------------------------------------
    # ショートカットのハンドラ
    # ------------------------------------------------------------------
    def on_new_tab_triggered(self):
        """Ctrl+T: アクティブなペインで、現在のフォルダの新規タブを開く"""
        self.get_active_pane().open_new_tab_current_path()

    def on_search_triggered(self):
        """Ctrl+Shift+F: アクティブなペインのフォルダを基準に検索ダイアログを開く"""
        from ui.search_dialog import SearchDialog  # 遅延import（未実装でも起動できるように）

        current_path = self.get_active_pane().get_current_path()
        SearchDialog(current_path, self).exec()

    # ------------------------------------------------------------------
    # ボタンのハンドラ
    # ------------------------------------------------------------------
    def show_disk_space_info(self):
        """ドライブ容量ポップアップ"""
        QApplication.setOverrideCursor(Qt.WaitCursor)  # 切断されたドライブがあると少し待たされることがある
        try:
            drives = get_drive_space_info()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "ディスク容量アナライザー", f"ドライブ情報を取得できませんでした:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        if drives:
            message = "\n\n".join(
                f"📁 ドライブ: {d['path']} ({d['name']})\n"
                f"    総容量: {d['total']:.2f} GB / "
                f"使用中: {d['used']:.2f} GB ({d['percent']:.1f}%)"
                for d in drives
            )
        else:
            message = "有効なドライブが見つかりませんでした。"

        QMessageBox.information(self, "ディスク容量アナライザー", message)

    def open_file_tools(self):
        """ファイル暗号化/完全削除ダイアログを開く"""
        FileToolsDialog(self).exec()

    def open_sftp_dialog(self):
        """SFTP接続ダイアログを開く"""
        SFTPConnectDialog(self).exec()

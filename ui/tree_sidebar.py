from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeView, QFileSystemModel, QMenu
from PySide6.QtCore import QDir, QModelIndex, QPersistentModelIndex, Signal, Qt
from PySide6.QtGui import QCursor, QMouseEvent

class EditableFileSystemModel(QFileSystemModel):
    def flags(self, index: QModelIndex):
        default_flags = super().flags(index)
        return default_flags | Qt.ItemIsEditable


class CustomTreeView(QTreeView):
    """
    - 左クリック: ペインは移動せず、フォルダの展開 / 折りたたみと選択のみ
    - ダブルクリック: 名前の変更はせず、通常のツリー展開・選択のみ
    - 右クリック1回: 左右ペインの新しいタブで開く ＋ 名前の変更メニューを表示
    - Ctrl + Enter / F2: インラインリネーム開始
    """
    open_in_pane_signal = Signal(str, str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.setRootIndex(QModelIndex())
        
        self.setEditTriggers(QTreeView.NoEditTriggers)
        
        for i in range(1, model.columnCount()):
            self.setColumnHidden(i, True)

        # 左クリック押下時点のインデックスと展開状態 (離した時の開閉判定に使う)
        self._press_index = QPersistentModelIndex()
        self._press_expanded = False

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            index = self.indexAt(event.pos())
            self._press_index = QPersistentModelIndex(index)
            self._press_expanded = index.isValid() and self.isExpanded(index)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        super().mouseReleaseEvent(event)

        if event.button() == Qt.LeftButton:
            # 左クリックではペインを移動しない。フォルダの展開 / 折りたたみだけを行う
            index = self.indexAt(event.pos())
            press_index = QModelIndex(self._press_index)
            self._press_index = QPersistentModelIndex()
            if (
                index.isValid()
                and press_index.isValid()
                and index == press_index
                and self.model().isDir(index)
                and self.isExpanded(index) == self._press_expanded
            ):
                # 展開インジケータのクリックは押下時に既定動作で開閉済みなので、
                # 状態が変わっていない (項目本体のクリック) 場合だけここで開閉する
                if self.isExpanded(index):
                    self.collapse(index)
                else:
                    self.expand(index)
            return

        if event.button() == Qt.RightButton:
            index = self.indexAt(event.pos())
            if index.isValid():
                model = self.model()
                path = model.filePath(index)
                
                menu = QMenu(self)
                
                action_left = None
                action_right = None
                if model.isDir(index):
                    action_left = menu.addAction("📁 左ペインで開く（新しいタブ）")
                    action_right = menu.addAction("📁 右ペインで開く（新しいタブ）")
                    menu.addSeparator()
                
                action_rename = menu.addAction("✏️ 名前の変更 (F2 / Ctrl+Enter)")
                
                selected_action = menu.exec(QCursor.pos())
                
                if selected_action == action_left:
                    self.open_in_pane_signal.emit(path, "left")
                elif selected_action == action_right:
                    self.open_in_pane_signal.emit(path, "right")
                elif selected_action == action_rename:
                    self.edit(index)

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier) or event.key() == Qt.Key_F2:
            index = self.currentIndex()
            if index.isValid():
                self.edit(index)
                event.accept()
                return
        super().keyPressEvent(event)


class TreeSidebar(QWidget):
    
    @property
    def open_in_pane_signal(self):
        return self.tree_view.open_in_pane_signal

    def __init__(self, parent=None):
        super().__init__(parent)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.model = EditableFileSystemModel()
        self.model.setRootPath("") 
        
        self.tree_view = CustomTreeView(self.model, self)
        
        layout.addWidget(self.tree_view)

    def select_path(self, path: str):
        """ペインの現在地にツリーの選択を合わせる (親フォルダを展開して表示する)。"""
        index = self.model.index(path)
        if index.isValid():
            parent = index.parent()
            while parent.isValid():
                self.tree_view.expand(parent)
                parent = parent.parent()
            self.tree_view.setCurrentIndex(index)
            self.tree_view.scrollTo(index)
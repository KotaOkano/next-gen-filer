from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeView, QFileSystemModel, QMenu
from PySide6.QtCore import QDir, QModelIndex, Signal, Qt
from PySide6.QtGui import QCursor, QMouseEvent

class EditableFileSystemModel(QFileSystemModel):
    def flags(self, index: QModelIndex):
        default_flags = super().flags(index)
        return default_flags | Qt.ItemIsEditable


class CustomTreeView(QTreeView):
    """
    - ダブルクリック: 名前の変更はせず、通常のツリー展開・選択のみ
    - 右クリック1回: 左右ペイン選択 ＋ 名前の変更メニューを表示
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

    def mouseReleaseEvent(self, event: QMouseEvent):
        super().mouseReleaseEvent(event)
        
        if event.button() == Qt.RightButton:
            index = self.indexAt(event.pos())
            if index.isValid():
                model = self.model()
                path = model.filePath(index)
                
                menu = QMenu(self)
                
                action_left = None
                action_right = None
                if model.isDir(index):
                    action_left = menu.addAction("📁 左ペインで開く")
                    action_right = menu.addAction("📁 右ペインで開く")
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
        index = self.model.index(path)
        if index.isValid():
            self.tree_view.setCurrentIndex(index)
            self.tree_view.scrollTo(index)
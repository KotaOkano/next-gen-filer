import os
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit
from PySide6.QtGui import QPixmap, QKeyEvent
from PySide6.QtCore import Qt

class QuickLookDialog(QDialog):
    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setWindowTitle(f"Quick Look - {os.path.basename(file_path)}")
        self.resize(800, 600)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        ext = os.path.splitext(self.file_path)[1].lower()

        # 画像ファイルの場合
        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp']:
            label = QLabel()
            pixmap = QPixmap(self.file_path)
            label.setPixmap(pixmap.scaled(780, 580, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label)

        # テキスト・ソースコード系の場合
        elif ext in ['.txt', '.py', '.md', '.json', '.html', '.css', '.js', '.csv', '.log']:
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            try:
                with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(50000)  # 最大50KBまで読み込み
                text_edit.setPlainText(content)
            except Exception as e:
                text_edit.setPlainText(f"ファイルを読み込めませんでした: {e}")
            layout.addWidget(text_edit)

        # その他のファイルの場合
        else:
            label = QLabel(f"プレビュー未対応の形式です\nファイル名: {os.path.basename(self.file_path)}\nパス: {self.file_path}")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label)

    def keyPressEvent(self, event: QKeyEvent):
        # スペースキーまたはEscキーでダイアログを閉じる
        if event.key() == Qt.Key_Space or event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
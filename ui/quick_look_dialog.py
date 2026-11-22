import os
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit
from PySide6.QtGui import QPixmap, QKeyEvent
from PySide6.QtCore import Qt, QUrl

IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp']
TEXT_EXTS = ['.txt', '.py', '.md', '.json', '.css', '.js', '.csv', '.log']
HTML_EXTS = ['.html', '.htm']


class QuickLookDialog(QDialog):
    """スペースキーで開く簡易プレビュー。画像 / テキスト / PDF / HTML に対応する。

    PDF (PySide6_Pdf) と HTML (PySide6_WebEngine) は重いので、開くときにだけ読み込む。
    ライブラリが無い環境では落とさず、未対応の案内を出す。
    """

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setWindowTitle(f"Quick Look - {os.path.basename(file_path)}")
        self.resize(800, 600)
        self._pdf_document = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self._layout = layout

        ext = os.path.splitext(self.file_path)[1].lower()

        # 画像ファイルの場合
        if ext in IMAGE_EXTS:
            label = QLabel()
            pixmap = QPixmap(self.file_path)
            label.setPixmap(pixmap.scaled(780, 580, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label)

        # PDF の場合
        elif ext == '.pdf':
            if not self._show_pdf():
                layout.addWidget(self._fallback_label("PDFプレビューを読み込めませんでした\n（PySide6_Pdf が必要です）"))

        # HTML の場合 (WebEngine が無ければソース表示に切り替える)
        elif ext in HTML_EXTS:
            if not self._show_html():
                layout.addWidget(self._text_view())

        # テキスト・ソースコード系の場合
        elif ext in TEXT_EXTS:
            layout.addWidget(self._text_view())

        # その他のファイルの場合
        else:
            layout.addWidget(self._fallback_label(
                f"プレビュー未対応の形式です\nファイル名: {os.path.basename(self.file_path)}\nパス: {self.file_path}"
            ))

    def _text_view(self) -> QTextEdit:
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(50000)  # 最大50KBまで読み込み
            text_edit.setPlainText(content)
        except Exception as e:
            text_edit.setPlainText(f"ファイルを読み込めませんでした: {e}")
        return text_edit

    def _fallback_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        return label

    def _show_pdf(self) -> bool:
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView
        except ImportError:
            return False
        document = QPdfDocument(self)
        document.load(self.file_path)
        if document.status() != QPdfDocument.Status.Ready:
            return False
        view = QPdfView(self)
        view.setDocument(document)
        view.setPageMode(QPdfView.PageMode.MultiPage)
        self._pdf_document = document  # ビューが使う間、破棄されないように保持する
        self._layout.addWidget(view)
        return True

    def _show_html(self) -> bool:
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            return False
        view = QWebEngineView(self)
        # ローカルHTMLのプレビュー専用: スクリプトと外部通信は無効化する
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            WA = QWebEngineSettings.WebAttribute
            settings = view.settings()
            settings.setAttribute(WA.JavascriptEnabled, False)
            settings.setAttribute(WA.LocalContentCanAccessRemoteUrls, False)
            settings.setAttribute(WA.PluginsEnabled, False)
        except Exception:
            pass
        view.setUrl(QUrl.fromLocalFile(os.path.abspath(self.file_path)))
        self._layout.addWidget(view)
        return True

    def keyPressEvent(self, event: QKeyEvent):
        # スペースキーまたはEscキーでダイアログを閉じる
        if event.key() == Qt.Key_Space or event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

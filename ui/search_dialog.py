import os
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, 
    QPushButton, QTableWidget, QTableWidgetItem, QLabel
)

class SearchWorker(QThread):
    found_file = Signal(str, str, str) # パス, サイズ, 更新日時

    def __init__(self, root_dir, keyword):
        super().__init__()
        self.root_dir = root_dir
        self.keyword = keyword.lower()
        self.is_running = True

    def run(self):
        try:
            for current_root, dirs, files in os.walk(self.root_dir):
                if not self.is_running:
                    break
                for file in files:
                    if not self.is_running:
                        break
                    if self.keyword in file.lower():
                        full_path = os.path.join(current_root, file)
                        try:
                            stat = os.stat(full_path)
                            size_str = f"{stat.st_size / 1024:.1f} KB"
                            # 簡易的なタイムスタンプ整形などは適宜
                            self.found_file.emit(full_path, size_str, "---")
                        except Exception:
                            pass
        except Exception:
            pass

    def stop(self):
        self.is_running = False


class SearchDialog(QDialog):
    def __init__(self, current_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("非同期再帰ファイル検索 (Ctrl+Shift+F)")
        self.resize(800, 500)
        self.search_root = current_path if current_path and os.path.isdir(current_path) else "C:\\"
        self.worker = None

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 検索条件入力レイアウト
        input_layout = QHBoxLayout()
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("検索キーワードを入力...")
        self.keyword_input.returnPressed.connect(self.start_search)
        input_layout.addWidget(self.keyword_input)

        self.search_btn = QPushButton("検索開始")
        self.search_btn.clicked.connect(self.start_search)
        input_layout.addWidget(self.search_btn)
        layout.addLayout(input_layout)

        # 結果表示テーブル
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ファイルパス", "サイズ", "更新日時"])
        self.table.setColumnWidth(0, 500)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(2, 150)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.doubleClicked.connect(self.open_file_location)
        layout.addWidget(self.table)

        self.status_label = QLabel(f"検索対象ルート: {self.search_root}")
        layout.addWidget(self.status_label)

    def start_search(self):
        keyword = self.keyword_input.text().strip()
        if not keyword:
            return

        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        self.table.setRowCount(0)
        self.status_label.setText("検索中...")

        self.worker = SearchWorker(self.search_root, keyword)
        self.worker.found_file.connect(self.add_result_row)
        self.worker.finished.connect(lambda: self.status_label.setText("検索完了"))
        self.worker.start()

    def add_result_row(self, path, size, date):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(path))
        self.table.setItem(row, 1, QTableWidgetItem(size))
        self.table.setItem(row, 2, QTableWidgetItem(date))

    def open_file_location(self):
        row = self.table.currentRow()
        if row >= 0:
            path_item = self.table.item(row, 0)
            if path_item:
                path = path_item.text()
                try:
                    os.system(f'explorer /select,"{path}"')
                except Exception:
                    pass

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        event.accept()
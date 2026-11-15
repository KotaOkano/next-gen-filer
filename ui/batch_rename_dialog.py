import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QTableWidget, QTableWidgetItem, QComboBox, QMessageBox, QHeaderView
)
from PySide6.QtCore import Qt

class BatchRenameDialog(QDialog):
    """複数ファイルの一括リネームを行うダイアログ"""
    def __init__(self, file_paths: list, parent=None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.setWindowTitle("一括リネーム (Batch Rename)")
        self.resize(700, 500)
        self.init_ui()
        self.update_preview()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        # 操作設定エリア
        settings_layout = QHBoxLayout()
        
        settings_layout.addWidget(QLabel("モード:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["文字列置換", "連番付与", "接頭辞・接尾辞追加"])
        self.mode_combo.currentIndexChanged.connect(self.update_preview)
        settings_layout.addWidget(self.mode_combo)
        
        layout.addLayout(settings_layout)

        # パラメータ入力エリア
        self.param_layout = QHBoxLayout()
        
        # 文字列置換用ウィジェット
        self.lbl_find = QLabel("検索:")
        self.txt_find = QLineEdit()
        self.txt_find.textChanged.connect(self.update_preview)
        
        self.lbl_replace = QLabel("置換後:")
        self.txt_replace = QLineEdit()
        self.txt_replace.textChanged.connect(self.update_preview)
        
        self.param_layout.addWidget(self.lbl_find)
        self.param_layout.addWidget(self.txt_find)
        self.param_layout.addWidget(self.lbl_replace)
        self.param_layout.addWidget(self.txt_replace)
        
        layout.addLayout(self.param_layout)

        # プレビュー用テーブル
        layout.addWidget(QLabel("プレビュー (変更前 ➔ 変更後)"))
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["元のファイル名", "新しいファイル名"])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.preview_table.setAlternatingRowColors(True)
        layout.addWidget(self.preview_table)

        # 実行・キャンセルボタン
        btn_layout = QHBoxLayout()
        self.btn_execute = QPushButton("リネームを実行")
        self.btn_execute.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 6px;")
        self.btn_execute.clicked.connect(self.execute_rename)
        
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel if hasattr(self, 'btn_cancel') else btn_cancel)
        btn_layout.addWidget(self.btn_execute)
        layout.addLayout(btn_layout)

        # モード変更時の入力欄切り替え
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)

    def on_mode_changed(self, index):
        while self.param_layout.count():
            item = self.param_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if index == 0:  # 文字列置換
            self.lbl_find = QLabel("検索:")
            self.txt_find = QLineEdit()
            self.txt_find.textChanged.connect(self.update_preview)
            self.lbl_replace = QLabel("置換後:")
            self.txt_replace = QLineEdit()
            self.txt_replace.textChanged.connect(self.update_preview)
            
            self.param_layout.addWidget(self.lbl_find)
            self.param_layout.addWidget(self.txt_find)
            self.param_layout.addWidget(self.lbl_replace)
            self.param_layout.addWidget(self.txt_replace)
            
        elif index == 1:  # 連番付与
            self.lbl_find = QLabel("接頭辞:")
            self.txt_find = QLineEdit()
            self.txt_find.setPlaceholderText("例: photo_")
            self.txt_find.textChanged.connect(self.update_preview)
            
            self.lbl_replace = QLabel("開始番号:")
            self.txt_replace = QLineEdit("1")
            self.txt_replace.textChanged.connect(self.update_preview)
            
            self.param_layout.addWidget(self.lbl_find)
            self.param_layout.addWidget(self.txt_find)
            self.param_layout.addWidget(self.lbl_replace)
            self.param_layout.addWidget(self.txt_replace)
            
        elif index == 2:  # 接頭辞・接尾辞追加
            self.lbl_find = QLabel("追加する前:")
            self.txt_find = QLineEdit()
            self.txt_find.textChanged.connect(self.update_preview)
            
            self.lbl_replace = QLabel("追加する後:")
            self.txt_replace = QLineEdit()
            self.txt_replace.textChanged.connect(self.update_preview)
            
            self.param_layout.addWidget(self.lbl_find)
            self.param_layout.addWidget(self.txt_find)
            self.param_layout.addWidget(self.lbl_replace)
            self.param_layout.addWidget(self.txt_replace)

        self.update_preview()

    def generate_new_name(self, original_name, index):
        ext = os.path.splitext(original_name)[1]
        stem = os.path.splitext(original_name)[0]
        mode = self.mode_combo.currentIndex()

        if mode == 0:  # 置換
            find_str = self.txt_find.text()
            replace_str = self.txt_replace.text()
            if not find_str:
                return original_name
            new_stem = stem.replace(find_str, replace_str)
            return new_stem + ext

        elif mode == 1:  # 連番
            prefix = self.txt_find.text()
            try:
                start_num = int(self.txt_replace.text() or 1)
            except ValueError:
                start_num = 1
            num_str = f"{start_num + index:03d}"  # 3桁の連番 (001, 002...)
            return f"{prefix}{num_str}{ext}"

        elif mode == 2:  # 前後追加
            prefix = self.txt_find.text()
            suffix = self.txt_replace.text()
            return f"{prefix}{stem}{suffix}{ext}"

        return original_name

    def update_preview(self):
        self.preview_table.setRowCount(len(self.file_paths))
        for i, path in enumerate(self.file_paths):
            orig_name = os.path.basename(path)
            new_name = self.generate_new_name(orig_name, i)
            
            self.preview_table.setItem(i, 0, QTableWidgetItem(orig_name))
            
            new_item = QTableWidgetItem(new_name)
            if orig_name != new_name:
                new_item.setForeground(Qt.darkGreen)
            self.preview_table.setItem(i, 1, new_item)

    def execute_rename(self):
        success_count = 0
        error_messages = []

        for i, old_path in enumerate(self.file_paths):
            dir_name = os.path.dirname(old_path)
            orig_name = os.path.basename(old_path)
            new_name = self.generate_new_name(orig_name, i)
            
            if orig_name == new_name:
                continue

            new_path = os.path.join(dir_name, new_name)
            try:
                os.rename(old_path, new_path)
                success_count += 1
            except Exception as e:
                error_messages.append(f"{orig_name} ➔ {new_name}: {e}")

        if error_messages:
            QMessageBox.warning(self, "完了（一部エラー）", f"{success_count}件成功しましたが、エラーが発生しました:\n" + "\n".join(error_messages))
        else:
            QMessageBox.information(self, "成功", f"{success_count}件のファイルを正常に一括リネームしました！")
        
        self.accept()
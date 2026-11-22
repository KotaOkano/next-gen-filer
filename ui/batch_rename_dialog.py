import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QTableWidget, QTableWidgetItem, QComboBox, QMessageBox, QHeaderView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from core.file_ops import validate_item_name

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

    def _build_plan(self):
        """リネーム計画を検証して返す。

        戻り値: [ [old_path, orig_name, new_name, error], ... ]
        error は None (問題なし) か、実行できない理由の文字列。
        名前の検証・変更後の重複・既存ファイルとの衝突をここで一括チェックする。
        リネーム対象同士の名前の入れ替え (a→b, b→a) は衝突扱いにせず、
        実行時に一時名を経由して処理する。
        """
        entries = []
        for i, old_path in enumerate(self.file_paths):
            orig_name = os.path.basename(old_path)
            new_name = self.generate_new_name(orig_name, i)
            error = None
            if new_name != orig_name:
                try:
                    new_name = validate_item_name(new_name)
                except ValueError as e:
                    error = str(e)
            entries.append([old_path, orig_name, new_name, error])

        # 変更後の名前の重複 (Windows は大文字小文字を区別しない)
        seen = {}
        for idx, e in enumerate(entries):
            if e[3] is not None or e[1] == e[2]:
                continue
            key = (os.path.dirname(e[0]).casefold(), e[2].casefold())
            if key in seen:
                e[3] = "変更後の名前が重複しています"
                other = entries[seen[key]]
                if other[3] is None:
                    other[3] = "変更後の名前が重複しています"
            else:
                seen[key] = idx

        # 既存ファイルとの衝突。「その名前のファイル自体もリネームで退く」場合だけは
        # 衝突扱いにしない (a→b, b→c の連鎖を許可するため)。
        # 名前が変わらないファイルが居座っている名前へのリネームは衝突として弾く。
        moving = {
            os.path.normpath(e[0]).casefold()
            for e in entries
            if e[3] is None and e[1] != e[2]
        }
        for e in entries:
            if e[3] is not None or e[1] == e[2]:
                continue
            target = os.path.join(os.path.dirname(e[0]), e[2])
            if os.path.exists(target) and os.path.normpath(target).casefold() not in moving:
                e[3] = "同じ名前のファイルが既にあります"
        return entries

    def update_preview(self):
        plan = self._build_plan()
        self.preview_table.setRowCount(len(plan))
        for i, (path, orig_name, new_name, error) in enumerate(plan):
            self.preview_table.setItem(i, 0, QTableWidgetItem(orig_name))

            new_item = QTableWidgetItem(new_name)
            if error is not None:
                new_item.setForeground(QColor("#d32f2f"))
                new_item.setToolTip(error)
            elif orig_name != new_name:
                new_item.setForeground(Qt.darkGreen)
            self.preview_table.setItem(i, 1, new_item)

    def execute_rename(self):
        plan = self._build_plan()

        # 検証で弾かれたものがあれば、1件もリネームせずに理由を示す
        invalid = [e for e in plan if e[3] is not None]
        if invalid:
            lines = [f"{e[1]} ➔ {e[2]}: {e[3]}" for e in invalid[:10]]
            if len(invalid) > 10:
                lines.append(f"…他 {len(invalid) - 10} 件")
            QMessageBox.warning(
                self, "リネームできません",
                "次の項目に問題があるため、リネームを実行しませんでした:\n" + "\n".join(lines))
            return

        targets = [e for e in plan if e[1] != e[2]]
        if not targets:
            QMessageBox.information(self, "一括リネーム", "変更されるファイルがありません。")
            return

        # 名前の入れ替え・連鎖 (a→b, b→c) に対応するため、全件を一時名へ退避してから本名にする
        temp_paths = {}
        moved = []
        try:
            for idx, (old_path, orig_name, new_name, _err) in enumerate(targets):
                dir_name = os.path.dirname(old_path)
                temp = os.path.join(dir_name, f".ngf-rename-{os.getpid()}-{idx}.tmp")
                n = 1
                while os.path.exists(temp):
                    temp = os.path.join(dir_name, f".ngf-rename-{os.getpid()}-{idx}-{n}.tmp")
                    n += 1
                os.rename(old_path, temp)
                temp_paths[old_path] = temp
                moved.append(old_path)
        except Exception as e:
            # 退避の途中で失敗したら、退避済みを元の名前に戻す
            for old_path in moved:
                try:
                    os.rename(temp_paths[old_path], old_path)
                except OSError:
                    pass
            QMessageBox.warning(self, "エラー", f"リネームの準備中に失敗しました: {e}")
            return

        success_count = 0
        error_messages = []
        for old_path, orig_name, new_name, _err in targets:
            temp = temp_paths[old_path]
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(temp, new_path)
                success_count += 1
            except Exception as e:
                error_messages.append(f"{orig_name} ➔ {new_name}: {e} (一時名 {os.path.basename(temp)} のまま残っています)")

        if error_messages:
            QMessageBox.warning(self, "完了（一部エラー）", f"{success_count}件成功しましたが、エラーが発生しました:\n" + "\n".join(error_messages))
            return  # エラー時はダイアログを閉じず、状況を確認できるようにする
        QMessageBox.information(self, "成功", f"{success_count}件のファイルを正常に一括リネームしました！")
        self.accept()
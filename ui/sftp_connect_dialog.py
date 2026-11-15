from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QMessageBox, QTableWidget, 
    QTableWidgetItem, QHeaderView, QGroupBox
)
from core.network_fs import SFTPManager
from pathlib import Path

class SFTPConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SFTP / リモートサーバー接続")
        self.resize(650, 500)

        known_hosts_path = str(Path.home() / ".ssh" / "known_hosts")
        self.sftp_manager = SFTPManager(known_hosts_path)

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        # 1. 接続情報入力グループ
        form_group = QGroupBox("接続パラメータ")
        form_layout = QVBoxLayout(form_group)

        # ホスト・ポート
        hp_layout = QHBoxLayout()
        hp_layout.addWidget(QLabel("ホスト (IP/ドメイン):"))
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("例: 192.168.1.50 または example.com")
        hp_layout.addWidget(self.host_input)

        hp_layout.addWidget(QLabel("ポート:"))
        self.port_input = QLineEdit("22")
        self.port_input.setMaximumWidth(80)
        hp_layout.addWidget(self.port_input)
        form_layout.addLayout(hp_layout)

        # ユーザー名
        user_layout = QHBoxLayout()
        user_layout.addWidget(QLabel("ユーザー名:"))
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("例: root, ubuntu")
        user_layout.addWidget(self.user_input)
        form_layout.addLayout(user_layout)

        # パスワード
        pwd_layout = QHBoxLayout()
        pwd_layout.addWidget(QLabel("パスワード:"))
        self.pwd_input = QLineEdit()
        self.pwd_input.setEchoMode(QLineEdit.Password)
        pwd_layout.addWidget(self.pwd_input)
        form_layout.addLayout(pwd_layout)

        # 接続ボタン
        self.btn_connect = QPushButton("🔌 SFTPサーバーに接続")
        self.btn_connect.setStyleSheet("background-color: #1f6feb; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        self.btn_connect.clicked.connect(self.handle_connect)
        form_layout.addWidget(self.btn_connect)

        main_layout.addWidget(form_group)

        # 2. 接続成功時のファイルプレビューテーブル
        preview_group = QGroupBox("リモートファイル一覧プレビュー")
        preview_layout = QVBoxLayout(preview_group)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ファイル名", "サイズ", "種類", "更新日時"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        preview_layout.addWidget(self.table)

        main_layout.addWidget(preview_group)

  
    def handle_connect(self):
        host = self.host_input.text().strip()
        port_str = self.port_input.text().strip()
        username = self.user_input.text().strip()
        password = self.pwd_input.text()

        if not host or not username:
            QMessageBox.warning(self, "入力エラー", "ホスト名とユーザー名は必須です。")
            return

        try:
            port = int(port_str)
        except ValueError:
            QMessageBox.warning(self, "入力エラー", "ポート番号は半角数字で入力してください。")
            return

        try:
            # 接続実行
            self.sftp_manager.connect(host, port, username, password if password else None)
            QMessageBox.information(self, "接続成功", f"SFTPサーバー ({host}:{port}) への接続に成功しました！")
            
            # ディレクトリ一覧を取得してテーブルに表示
            self.load_remote_files()

        except Exception as e:
            QMessageBox.critical(self, "接続失敗", f"サーバーへの接続に失敗しました:\n{e}")

    def load_remote_files(self):
        try:
            items = self.sftp_manager.list_dir()
            self.table.setRowCount(len(items))
            
            for row, item in enumerate(items):
                self.table.setItem(row, 0, QTableWidgetItem(item["name"]))
                
                size_str = f"{item['size']:,} bytes" if not item["is_dir"] else "<DIR>"
                self.table.setItem(row, 1, QTableWidgetItem(size_str))
                
                type_str = "フォルダ" if item["is_dir"] else "ファイル"
                self.table.setItem(row, 2, QTableWidgetItem(type_str))
                
                self.table.setItem(row, 3, QTableWidgetItem(str(item["mtime"])))
        except Exception as e:
            QMessageBox.warning(self, "警告", f"ファイル一覧の取得中にエラーが発生しました: {e}")

    def closeEvent(self, event):
        self.sftp_manager.disconnect()
        event.accept()
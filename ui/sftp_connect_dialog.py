"""SFTP 接続ダイアログ。

core/network_fs.SFTPManager の現在の仕様に合わせている:
- 一覧は RemoteEntry (データクラス) で返る
- 未確認のホスト鍵は、フィンガープリントを見せてユーザーが信頼した場合だけ保存して再接続する
- 鍵認証 (秘密鍵ファイル + パスフレーズ) にも対応する
- known_hosts は OpenSSH の ~/.ssh/known_hosts とは別の、アプリ専用ファイルを使う
  (アプリが書き換えて OpenSSH 側の記録を壊さないため)
"""
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from core import file_ops
from core.network_fs import (
    ChangedHostKeyError,
    PassphraseRequired,
    SFTPManager,
    UnknownHostKeyError,
)


class SFTPConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SFTP / リモートサーバー接続")
        self.resize(650, 560)

        known_hosts_path = str(Path.home() / ".nextgenfiler" / "known_hosts")
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

        # 秘密鍵ファイル (鍵認証を使う場合のみ)
        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("秘密鍵ファイル (任意):"))
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("鍵認証を使う場合は id_ed25519 などへのパス")
        key_layout.addWidget(self.key_input)
        browse_button = QPushButton("参照...")
        browse_button.clicked.connect(self._browse_key_file)
        key_layout.addWidget(browse_button)
        form_layout.addLayout(key_layout)

        # 秘密鍵のパスフレーズ
        pass_layout = QHBoxLayout()
        pass_layout.addWidget(QLabel("鍵のパスフレーズ (任意):"))
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        pass_layout.addWidget(self.pass_input)
        form_layout.addLayout(pass_layout)

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

    def _browse_key_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "秘密鍵ファイルを選択", str(Path.home() / ".ssh"))
        if path:
            self.key_input.setText(path)

    # ------------------------------------------------------------------
    # 接続 (ホスト鍵の確認とパスフレーズ入力を挟みながら再試行する)
    # ------------------------------------------------------------------
    def handle_connect(self):
        host = self.host_input.text().strip()
        port_str = self.port_input.text().strip()
        username = self.user_input.text().strip()
        password = self.pwd_input.text() or None
        key_filename = self.key_input.text().strip() or None
        passphrase = self.pass_input.text() or None

        if not host or not username:
            QMessageBox.warning(self, "入力エラー", "ホスト名とユーザー名は必須です。")
            return

        try:
            port = int(port_str)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "入力エラー", "ポート番号は 1〜65535 の半角数字で入力してください。")
            return

        while True:
            try:
                self.sftp_manager.connect(
                    host, port, username,
                    password=password, key_filename=key_filename, passphrase=passphrase,
                )
                break
            except UnknownHostKeyError as exc:
                if not self._confirm_unknown_host(exc.info):
                    return
                self.sftp_manager.trust_host_key(exc.info)
            except ChangedHostKeyError as exc:
                if not self._confirm_changed_host(exc.info):
                    return
                self.sftp_manager.forget_host(host, port)
                self.sftp_manager.trust_host_key(exc.info)
            except PassphraseRequired:
                text, ok = QInputDialog.getText(
                    self, "パスフレーズが必要です",
                    "秘密鍵のパスフレーズを入力してください:",
                    QLineEdit.Password,
                )
                if not ok:
                    return
                passphrase = text
            except Exception as exc:  # noqa: BLE001 - 理由をそのまま見せる
                QMessageBox.critical(self, "接続失敗", f"サーバーへの接続に失敗しました:\n{exc}")
                return

        QMessageBox.information(self, "接続成功", f"SFTPサーバー ({host}:{port}) への接続に成功しました！")
        self.load_remote_files()

    def _confirm_unknown_host(self, info) -> bool:
        reply = QMessageBox.question(
            self, "サーバーの確認",
            "初めて接続するサーバーです。サーバーの鍵が正しいか確認してください。\n\n"
            f"ホスト: {info.host}:{info.port}\n"
            f"鍵の種類: {info.key_type}\n"
            f"フィンガープリント: {info.fingerprint}\n\n"
            "この鍵を信頼して接続しますか？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _confirm_changed_host(self, info) -> bool:
        stored = "\n".join(
            f"  {key_type}: {fp}" for key_type, fp in self.sftp_manager.stored_fingerprints(info.host, info.port)
        ) or "  (なし)"
        reply = QMessageBox.warning(
            self, "サーバーの鍵が変わっています",
            "サーバーの鍵が、以前に確認したものと異なります。\n"
            "サーバーの入れ替え、または中間者攻撃の可能性があります。\n\n"
            f"保存済み:\n{stored}\n\n"
            f"今回提示された鍵:\n  {info.key_type}: {info.fingerprint}\n\n"
            "新しい鍵を信頼し直して接続しますか？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    # ------------------------------------------------------------------
    # 一覧表示 (RemoteEntry の属性で読む)
    # ------------------------------------------------------------------
    def load_remote_files(self):
        try:
            entries = self.sftp_manager.list_dir()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "警告", f"ファイル一覧の取得中にエラーが発生しました: {exc}")
            return

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.table.setItem(row, 0, QTableWidgetItem(entry.name))
            size_str = "<DIR>" if entry.is_dir else f"{file_ops.format_size(entry.size)}"
            self.table.setItem(row, 1, QTableWidgetItem(size_str))
            self.table.setItem(row, 2, QTableWidgetItem("フォルダ" if entry.is_dir else "ファイル"))
            mtime_str = (
                datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M") if entry.mtime else ""
            )
            self.table.setItem(row, 3, QTableWidgetItem(mtime_str))

    def closeEvent(self, event):
        self.sftp_manager.disconnect()
        event.accept()

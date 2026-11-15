"""ファイル暗号化 / 完全削除（シュレッダー）ダイアログ。

暗号化・復号・シュレッダーは別スレッドで実行し、進捗表示とキャンセルに対応する。
処理の実体は core/file_utils.py（Qt 非依存）にある。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import file_ops, file_utils

MIN_PASSWORD_LENGTH = 8

ENCRYPT_BUTTON_STYLE = (
    "background-color: #1f6feb; color: white; font-weight: bold; padding: 8px; border-radius: 4px;"
)
DECRYPT_BUTTON_STYLE = (
    "background-color: #238636; color: white; font-weight: bold; padding: 8px; border-radius: 4px;"
)
SHRED_BUTTON_STYLE = (
    "background-color: #da3633; color: white; font-weight: bold; padding: 10px; border-radius: 4px;"
)


def _stage(report, start: float, end: float):
    """処理全体の start%〜end% を受け持つ進捗コールバック (done, total) を作る"""

    def callback(done: int, total: int):
        fraction = done / total if total else 1.0
        report(start + (end - start) * fraction)

    return callback


class ToolWorker(QThread):
    """task(report, is_cancelled) を別スレッドで実行する"""

    progressed = Signal(int)  # 0〜100 (%)
    finished_with = Signal(str, object)  # ("ok" | "error" | "cancelled", 結果 / 例外 / None)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task
        self._cancelled = False
        self._last_percent = -1

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def report(self, percent: float):
        value = max(0, min(100, int(percent)))
        if value != self._last_percent:  # 変化したときだけ送る
            self._last_percent = value
            self.progressed.emit(value)

    def run(self):
        try:
            result = self._task(self.report, self.is_cancelled)
        except file_utils.OperationCancelled:
            self.finished_with.emit("cancelled", None)
        except Exception as exc:  # noqa: BLE001 - 画面にメッセージとして見せる
            self.finished_with.emit("error", exc)
        else:
            self.finished_with.emit("ok", result)


class FileToolsDialog(QDialog):
    """ファイルの暗号化 / 復号と、完全削除（シュレッダー）を行うダイアログ"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ファイル暗号化 / 完全削除")
        self.resize(650, 520)

        self._worker: "ToolWorker | None" = None
        self._progress: "QProgressDialog | None" = None
        self._busy = ("", None, "")  # (操作名, 成功時の処理, キャンセル時のメッセージ)

        self.init_ui()

    # ==================================================================
    # UI 構築
    # ==================================================================
    def init_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_crypto_tab(), "🔒 AES暗号化 / 復号")
        self.tabs.addTab(self.create_shredder_tab(), "🔥 完全削除 (シュレッダー)")
        layout.addWidget(self.tabs)

    def _create_path_row(self, line_edit: QLineEdit, placeholder: str) -> QHBoxLayout:
        line_edit.setPlaceholderText(placeholder)
        row = QHBoxLayout()
        row.addWidget(line_edit)
        browse = QPushButton("参照...")
        browse.clicked.connect(lambda: self.browse_file(line_edit))
        row.addWidget(browse)
        return row

    @staticmethod
    def _create_password_field(placeholder: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText(placeholder)
        return field

    def create_crypto_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        description = QLabel(
            "パスワードを使用してファイルを暗号化（または復号）します。\n"
            "⚠️ パスワードを忘れると、ファイルは二度と開けません。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.crypto_path_input = QLineEdit()
        layout.addLayout(
            self._create_path_row(self.crypto_path_input, "対象ファイルを選択 (.enc または 通常ファイル)...")
        )

        pwd_row = QHBoxLayout()
        pwd_row.addWidget(QLabel("パスワード:"))
        self.crypto_pwd_input = self._create_password_field("暗号化・復号用のパスワード")
        pwd_row.addWidget(self.crypto_pwd_input)
        layout.addLayout(pwd_row)

        confirm_row = QHBoxLayout()
        confirm_row.addWidget(QLabel("パスワード（確認）:"))
        self.crypto_pwd_confirm = self._create_password_field("もう一度入力（暗号化のときだけ使います）")
        confirm_row.addWidget(self.crypto_pwd_confirm)
        layout.addLayout(confirm_row)

        self.delete_original_check = QCheckBox("暗号化に成功したら、元のファイルを完全削除する（シュレッダー）")
        layout.addWidget(self.delete_original_check)

        note = QLabel("※ 元のファイルは、上のチェックを入れない限り残ります。")
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        encrypt_button = QPushButton("🔒 ファイルを暗号化 (.enc)")
        encrypt_button.setStyleSheet(ENCRYPT_BUTTON_STYLE)
        encrypt_button.clicked.connect(self.handle_encrypt)
        buttons.addWidget(encrypt_button)

        decrypt_button = QPushButton("🔓 ファイルを復号")
        decrypt_button.setStyleSheet(DECRYPT_BUTTON_STYLE)
        decrypt_button.clicked.connect(self.handle_decrypt)
        buttons.addWidget(decrypt_button)
        layout.addLayout(buttons)

        layout.addStretch()
        return widget

    def create_shredder_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        warning = QLabel(
            "⚠️ 【警告】シュレッダーを実行すると、ファイルの内容がランダムなデータで上書きされた上で"
            "削除され、元に戻せません。通常のデータ復元ソフトでの復元は非常に困難になります。\n\n"
            "ただし、SSD・クラウド同期（OneDrive など）・シャドウコピーでは、古いデータが別の場所に"
            "残ることがあり、完全な消去は保証できません。特に重要なデータは、ドライブ全体の暗号化"
            "（BitLocker など）と併用してください。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #f85149; font-weight: bold; padding: 4px;")
        layout.addWidget(warning)

        self.shred_path_input = QLineEdit()
        layout.addLayout(self._create_path_row(self.shred_path_input, "完全に削除したいファイルを選択..."))

        passes_row = QHBoxLayout()
        passes_row.addWidget(QLabel("上書き回数:"))
        self.shred_passes_spin = QSpinBox()
        self.shred_passes_spin.setRange(1, 7)
        self.shred_passes_spin.setValue(file_utils.DEFAULT_SHRED_PASSES)
        self.shred_passes_spin.setToolTip("現在のHDDでは1回の上書きでも通常の復元は困難です。回数を増やすと時間がかかります。")
        passes_row.addWidget(self.shred_passes_spin)
        passes_row.addStretch()
        layout.addLayout(passes_row)

        shred_button = QPushButton("🔥 ファイルを完全に削除する (シュレッダー実行)")
        shred_button.setStyleSheet(SHRED_BUTTON_STYLE)
        shred_button.clicked.connect(self.handle_shred)
        layout.addWidget(shred_button)

        layout.addStretch()
        return widget

    # ==================================================================
    # 入力のヘルパー
    # ==================================================================
    def browse_file(self, line_edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "ファイルを選択", "", "すべてのファイル (*)")
        if path:
            line_edit.setText(path)

    @staticmethod
    def _clean_path(text: str) -> str:
        """前後の空白と引用符を外す（エクスプローラーの「パスのコピー」は引用符付き）"""
        return text.strip().strip('"').strip()

    def _warn(self, text: str):
        QMessageBox.warning(self, "エラー", text)

    def _confirm(self, title: str, text: str) -> bool:
        reply = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return reply == QMessageBox.Yes

    def _valid_file(self, path: str) -> bool:
        if path and os.path.isfile(path) and not os.path.islink(path):
            return True
        self._warn("有効なファイルを選択してください。（フォルダやシンボリックリンクは対象外です）")
        return False

    # ==================================================================
    # 暗号化
    # ==================================================================
    def handle_encrypt(self):
        path = self._clean_path(self.crypto_path_input.text())
        if not self._valid_file(path):
            return

        password = self.crypto_pwd_input.text()
        if not password:
            self._warn("パスワードを入力してください。")
            return
        if password != self.crypto_pwd_confirm.text():
            self._warn("パスワードが一致しません。入力ミスのまま暗号化すると、ファイルを開けなくなります。")
            return
        if len(password) < MIN_PASSWORD_LENGTH and not self._confirm(
            "短いパスワード",
            f"パスワードが {MIN_PASSWORD_LENGTH} 文字より短いため、推測されやすくなります。\nこのまま暗号化しますか？",
        ):
            return
        if file_utils.looks_encrypted(path) and not self._confirm(
            "二重暗号化の確認",
            "このファイルはすでに暗号化されているようです。\n復号したい場合は「復号」ボタンを使ってください。\n\nさらに暗号化しますか？",
        ):
            return

        delete_original = self.delete_original_check.isChecked()
        if delete_original and not self._confirm(
            "元ファイルの削除の確認",
            "暗号化と検証に成功したあと、元のファイルを完全削除します（元に戻せません）。\n\n"
            f"{os.path.normpath(path)}\n\nよろしいですか？",
        ):
            return

        def task(report, is_cancelled):
            if delete_original:
                encrypt_cb, verify_cb, shred_cb = _stage(report, 0, 40), _stage(report, 40, 50), _stage(report, 50, 100)
            else:
                encrypt_cb = verify_cb = shred_cb = _stage(report, 0, 100)

            output = file_utils.encrypt_file(path, password, progress=encrypt_cb, is_cancelled=is_cancelled)
            result = {"output": output, "deleted": False, "delete_error": None, "original_damaged": False}
            if not delete_original:
                return result

            # 暗号化ファイルを最後まで復号できると確認できてから、元のファイルを消す
            shred_started = False
            try:
                file_utils.verify_file(output, password, progress=verify_cb, is_cancelled=is_cancelled)
                shred_started = True
                file_utils.shred_file(path, progress=shred_cb, is_cancelled=is_cancelled)
                result["deleted"] = True
            except file_utils.OperationCancelled:
                result["delete_error"] = "キャンセルされました"
            except Exception as exc:  # noqa: BLE001
                result["delete_error"] = str(exc) or type(exc).__name__
            result["original_damaged"] = shred_started and not result["deleted"]
            return result

        self._run("暗号化", task, self._after_encrypt)

    def _after_encrypt(self, result: dict):
        output = os.path.normpath(result["output"])
        if result["deleted"]:
            QMessageBox.information(
                self, "暗号化成功", f"ファイルを暗号化しました:\n{output}\n\n元のファイルは完全削除しました。"
            )
        elif result["delete_error"]:
            text = (
                f"ファイルを暗号化しました:\n{output}\n\n"
                f"ただし、元のファイルの完全削除は完了しませんでした:\n{result['delete_error']}"
            )
            if result["original_damaged"]:
                text += "\n\n元のファイルは一部が上書きされている可能性があります。暗号化ファイルから復号して確認してください。"
            QMessageBox.warning(self, "暗号化は成功（元ファイルの削除は未完了）", text)
        else:
            QMessageBox.information(
                self,
                "暗号化成功",
                f"ファイルを暗号化しました:\n{output}\n\n※ 元のファイルは残っています。不要な場合は別途削除してください。",
            )
        self._clear_crypto_fields()

    # ==================================================================
    # 復号
    # ==================================================================
    def handle_decrypt(self):
        path = self._clean_path(self.crypto_path_input.text())
        if not self._valid_file(path):
            return

        password = self.crypto_pwd_input.text()
        if not password:
            self._warn("パスワードを入力してください。")
            return

        def task(report, is_cancelled):
            return file_utils.decrypt_file(path, password, progress=_stage(report, 0, 100), is_cancelled=is_cancelled)

        self._run("復号", task, self._after_decrypt)

    def _after_decrypt(self, output: str):
        QMessageBox.information(
            self,
            "復号成功",
            f"ファイルを復号しました:\n{os.path.normpath(output)}\n\n（同名のファイルがあった場合は、別名で保存しています）",
        )
        self._clear_crypto_fields()

    def _clear_crypto_fields(self):
        self.crypto_path_input.clear()
        self.crypto_pwd_input.clear()
        self.crypto_pwd_confirm.clear()

    # ==================================================================
    # シュレッダー
    # ==================================================================
    def handle_shred(self):
        path = self._clean_path(self.shred_path_input.text())
        if not self._valid_file(path):
            return

        passes = self.shred_passes_spin.value()
        size = file_ops.format_size(os.path.getsize(path))
        if not self._confirm(
            "最終確認 - シュレッダー",
            f"本当に以下のファイルを完全に削除しますか？\n\n{os.path.normpath(path)}\nサイズ: {size}\n\n"
            "（元に戻すことはできません）",
        ):
            return

        def task(report, is_cancelled):
            file_utils.shred_file(
                path, passes=passes, progress=_stage(report, 0, 100), is_cancelled=is_cancelled
            )

        self._run(
            "完全削除",
            task,
            self._after_shred,
            cancel_message="キャンセルしました。\nファイルは一部が上書きされた状態で残っています（中身は元に戻せません）。",
        )

    def _after_shred(self, _result):
        QMessageBox.information(
            self,
            "完了",
            "ファイルをランダムデータで上書きし、削除しました。\n\n"
            "※ SSD やクラウド同期のフォルダでは、古いデータが別の場所に残る可能性があります。",
        )
        self.shred_path_input.clear()

    # ==================================================================
    # バックグラウンド実行
    # ==================================================================
    def _run(self, title: str, task, on_success, cancel_message: str = "キャンセルしました。"):
        if self._worker is not None:
            return

        progress = QProgressDialog(f"{title}中...", "キャンセル", 0, 100, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(300)  # 一瞬で終わる処理ではダイアログを出さない
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        worker = ToolWorker(task, self)
        worker.progressed.connect(self._on_progress)
        worker.finished_with.connect(self._on_finished)
        progress.canceled.connect(worker.cancel)

        self._worker, self._progress = worker, progress
        self._busy = (title, on_success, cancel_message)
        worker.start()

    def _on_progress(self, percent: int):
        if self._progress is not None and not self._progress.wasCanceled():
            self._progress.setValue(percent)

    def _on_finished(self, status: str, payload):
        title, on_success, cancel_message = self._busy
        worker, progress = self._worker, self._progress
        self._worker = self._progress = None

        if progress is not None:
            progress.reset()
            progress.hide()
            progress.deleteLater()
        if worker is not None:
            worker.wait()
            worker.deleteLater()

        if status == "ok":
            on_success(payload)
        elif status == "cancelled":
            QMessageBox.information(self, title, cancel_message)
        else:
            self._show_error(title, payload)

    def _show_error(self, title: str, exc: Exception):
        if isinstance(exc, (file_utils.DecryptionError, file_utils.InvalidFileError)):
            text = str(exc)
        elif isinstance(exc, FileNotFoundError):
            text = f"ファイルが見つかりません:\n{exc}"
        else:
            text = f"{title}に失敗しました:\n{exc}"
        QMessageBox.critical(self, f"{title}エラー", text)

    # ==================================================================
    # 処理中はダイアログを閉じさせない
    # ==================================================================
    def reject(self):
        if self._worker is not None:
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            event.ignore()
            return
        super().closeEvent(event)

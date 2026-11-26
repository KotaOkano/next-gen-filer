from __future__ import annotations

import os

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from core import file_ops

# 表示名と hashlib のアルゴリズム名 (先頭が既定の SHA-256)
ALGORITHMS = [
    ("SHA-256", "sha256"),
    ("MD5", "md5"),
    ("SHA-1", "sha1"),
    ("SHA-512", "sha512"),
]


class HashWorker(QThread):
    """ハッシュ計算を別スレッドで実行し、UI を固めない。"""

    progressed = Signal(int)  # 進捗 (0-100 %)
    done = Signal(str)  # 計算結果 (16進文字列)
    failed = Signal(str)  # エラーメッセージ

    def __init__(self, path: str, algorithm: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.algorithm = algorithm
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        def progress(done_bytes: int, total_bytes: int):
            self.progressed.emit(int(done_bytes / total_bytes * 100) if total_bytes else 100)

        try:
            digest = file_ops.compute_file_hash(
                self.path,
                self.algorithm,
                progress=progress,
                is_cancelled=lambda: self._cancelled,
            )
        except file_ops.OperationCancelled:
            return  # キャンセル時は結果を出さない
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        if not self._cancelled:
            self.done.emit(digest)


class HashDialog(QDialog):
    """ファイルのハッシュ値を計算し、公式値などと照合するダイアログ。"""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self._worker: "HashWorker | None" = None
        self._digest = ""

        self.setWindowTitle("ハッシュを計算")
        self.setMinimumWidth(580)
        self.init_ui()
        self._start_calculation()

    def init_ui(self):
        layout = QVBoxLayout(self)

        name_label = QLabel(f"ファイル: {os.path.basename(self.path)}")
        layout.addWidget(name_label)
        path_label = QLabel(self.path)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(path_label)

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("アルゴリズム:"))
        self.algorithm_combo = QComboBox()
        for label, _name in ALGORITHMS:
            self.algorithm_combo.addItem(label)
        self.algorithm_combo.currentIndexChanged.connect(lambda _i: self._start_calculation())
        algo_row.addWidget(self.algorithm_combo)
        algo_row.addStretch()
        layout.addLayout(algo_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        result_row = QHBoxLayout()
        result_row.addWidget(QLabel("計算結果:"))
        self.result_edit = QLineEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("計算中...")
        result_row.addWidget(self.result_edit)
        self.copy_button = QPushButton("コピー")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_result)
        result_row.addWidget(self.copy_button)
        layout.addLayout(result_row)

        expected_row = QHBoxLayout()
        expected_row.addWidget(QLabel("照合する値:"))
        self.expected_edit = QLineEdit()
        self.expected_edit.setPlaceholderText("公式サイトなどのハッシュ値を貼り付けると照合します")
        self.expected_edit.textChanged.connect(self._update_match)
        expected_row.addWidget(self.expected_edit)
        layout.addLayout(expected_row)

        self.match_label = QLabel("")
        self.match_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.match_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.cancel_button = QPushButton("計算をキャンセル")
        self.cancel_button.clicked.connect(self._cancel_calculation)
        button_row.addWidget(self.cancel_button)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    # ------------------------------------------------------------------
    def _current_algorithm(self) -> str:
        return ALGORITHMS[self.algorithm_combo.currentIndex()][1]

    def is_calculating(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _start_calculation(self):
        self._stop_worker()
        self._digest = ""
        self.result_edit.setText("")
        self.progress_bar.setValue(0)
        self.copy_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._update_match()

        worker = HashWorker(self.path, self._current_algorithm(), self)
        worker.progressed.connect(self.progress_bar.setValue)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _stop_worker(self):
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            worker.wait()
            worker.deleteLater()

    def _cancel_calculation(self):
        if self._worker is not None:
            self._worker.cancel()
        self.cancel_button.setEnabled(False)
        self.match_label.setText("計算をキャンセルしました")
        self.match_label.setStyleSheet("font-weight: bold; color: #888;")

    def _on_done(self, digest: str):
        self._digest = digest
        self.result_edit.setText(digest)
        self.progress_bar.setValue(100)
        self.copy_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._update_match()

    def _on_failed(self, message: str):
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(False)
        self.match_label.setText(f"計算できませんでした: {message}")
        self.match_label.setStyleSheet("font-weight: bold; color: #d33;")

    def _copy_result(self):
        if self._digest:
            QGuiApplication.clipboard().setText(self._digest)

    def _update_match(self):
        expected = self.expected_edit.text()
        if not self._digest or not expected.strip():
            self.match_label.setText("")
            return
        if file_ops.hash_matches(self._digest, expected):
            self.match_label.setText("✓ 一致しました")
            self.match_label.setStyleSheet("font-weight: bold; color: #2a2;")
        else:
            self.match_label.setText("✗ 一致しません")
            self.match_label.setStyleSheet("font-weight: bold; color: #d33;")

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def reject(self):
        self._stop_worker()
        super().reject()

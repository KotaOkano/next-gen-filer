import os
from PySide6.QtCore import QThread, Signal

class FolderSizeWorker(QThread):
    finished = Signal(str, int)  # (folder_path, total_size_bytes)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        total_size = 0
        try:
            for root, dirs, files in os.walk(self.path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        if not os.path.islink(fp):
                            total_size += os.path.getsize(fp)
                    except (PermissionError, FileNotFoundError):
                        continue
        except Exception:
            pass
        self.finished.emit(self.path, total_size)
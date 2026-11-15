import os
from PySide6.QtCore import QThread, Signal

class FileWorker(QThread):
    finished = Signal(str, list)
    error = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            items = []
            with os.scandir(self.path) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat()
                        items.append({
                            "name": entry.name,
                            "is_dir": entry.is_dir(),
                            "size": stat.st_size if not entry.is_dir() else 0,
                            "mtime": stat.st_mtime,
                            "path": entry.path
                        })
                    except PermissionError:
                        continue
            self.finished.emit(self.path, items)
        except Exception as e:
            self.error.emit(str(e))
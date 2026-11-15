# NextGenFiler
# Copyright (c) 2026 岡野 航大 (Kota Okano) - https://github.com/KotaOkano
# Released under the MIT License

import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
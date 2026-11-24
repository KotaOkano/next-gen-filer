# NextGenFiler
# Copyright (c) 2026 岡野 航大 (Kota Okano) - https://github.com/KotaOkano
# Released under the MIT License

import sys
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from version import __version__

# Windows のタスクバーで Python のアイコンとして扱われないようにする ID
APP_USER_MODEL_ID = "KotaOkano.NextGenFiler"


def _set_app_user_model_id():
    """Windows: プロセスに AppUserModelID を付け、アプリアイコンで表示させる。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 - 失敗しても起動は続ける
        pass


def main():
    _set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setOrganizationName("KotaOkano")
    app.setApplicationName("NextGenFiler")
    app.setApplicationVersion(__version__)

    from ui.app_icon import create_app_icon

    app.setWindowIcon(create_app_icon())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
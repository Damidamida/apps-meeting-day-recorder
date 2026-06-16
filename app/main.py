import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.branding import APP_ICON_RESOURCE, WINDOWS_APP_ID
from app.runtime import resource_path
from app.ui.main_window import MainWindow


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except (AttributeError, OSError):
        return


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    icon_path = resource_path(APP_ICON_RESOURCE)
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())


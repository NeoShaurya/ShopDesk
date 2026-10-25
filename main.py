import sqlite3
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app import navkeys, theme
from app.db import connect, get_settings
from app.theme import STYLE
from app.ui.main_window import MainWindow


def icon_path():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / "icon.png"


def show_error(kind, value, tb):
    """Last line of defence: tell the user in plain words instead of silently dying."""
    if isinstance(value, sqlite3.IntegrityError) and "financial year" in str(value).lower():
        text = str(value)
    elif isinstance(value, ValueError):
        text = str(value)
    else:
        text = f"Something went wrong:\n{value}\n\nYour data is safe. If this keeps happening, take a backup and tell your developer."
        sys.__excepthook__(kind, value, tb)
    QMessageBox.warning(None, "ShopDesk", text)


if __name__ == "__main__":
    if sys.platform == "win32":                       # own taskbar icon instead of the generic Python one
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShopDesk.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("ShopDesk")
    if icon_path().exists():
        app.setWindowIcon(QIcon(str(icon_path())))
    sys.excepthook = show_error
    app.setStyleSheet(STYLE)
    theme.install(app)
    navkeys.install(app)
    con = connect()
    theme.set_dark(app, get_settings(con)["dark_mode"] == "1")
    win = MainWindow(con)
    win.show()
    sys.exit(app.exec())

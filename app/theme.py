STYLE = """
* { font-family: 'Segoe UI', sans-serif; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: #f1f5f9; }
QListWidget#nav { background: #0f1729; border: none; padding-top: 16px; outline: 0; }
QListWidget#nav::item { color: #cbd5e1; padding: 14px 20px; font-size: 15px; }
QListWidget#nav::item:hover { background: #1e293b; }
QListWidget#nav::item:selected { background: #2563eb; color: white; }
"""

LIST = ("QListWidget{background:white;border:1px solid #e2e8f0;border-radius:6px;outline:0;}"
        "QListWidget::item{padding:10px 12px;color:#0f172a;}"
        "QListWidget::item:selected{background:#dbeafe;color:#0f172a;}")
BTN = ("QPushButton{background:#2563eb;color:white;border:none;border-radius:6px;padding:9px 16px;font-weight:600;}"
       "QPushButton:hover{background:#1d4ed8;}QPushButton:disabled{background:#cbd5e1;color:#f8fafc;}")
BTN_GREY = ("QPushButton{background:white;color:#0f172a;border:1px solid #cbd5e1;border-radius:6px;padding:9px 16px;}"
            "QPushButton:hover{background:#f1f5f9;}QPushButton:disabled{background:#f1f5f9;color:#94a3b8;}")
INPUT = "padding:7px;border:1px solid #cbd5e1;border-radius:6px;background:white;"
TABLE = ("QTableWidget{background:white;alternate-background-color:#f8fafc;border:1px solid #e2e8f0;gridline-color:#f1f5f9;}"
         "QHeaderView::section{background:#f1f5f9;padding:8px;border:none;font-weight:600;color:#475569;}"
         "QTableWidget::item:selected{background:#dbeafe;color:#0f172a;}")


# ---------------------------------------------------------------- dark mode
# Widgets carry their colours in their own style sheets, so dark mode rewrites those colours (light -> dark)
# whenever a widget is shown or restyled. Printing and PDFs are not affected: they always stay black on white.
import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

DARK_PAGE = "#0b1120"
_MAP = {"#ffffff": "#1e293b", "#f8fafc": "#172033", "#f1f5f9": "#273449", "#e2e8f0": "#334155",
        "#cbd5e1": "#475569", "#0f172a": "#e2e8f0", "#475569": "#cbd5e1", "#64748b": "#94a3b8",
        "#dbeafe": "#1e3a8a", "#fee2e2": "#450a0a", "#fffbeb": "#422006", "#fcd34d": "#92400e",
        "#92400e": "#fcd34d", "#f0fdf4": "#052e16", "#86efac": "#166534", "#15803d": "#86efac"}
_RX = re.compile(r"(?i)(background(?:-color)?\s*:\s*)white\b|#[0-9a-f]{6}\b")
_dark = False
_original_style = None


def is_dark():
    return _dark


def to_dark(css):
    def sub(m):
        if m.group(1):                                   # 'background: white' only; 'color: white' stays white
            return m.group(1) + _MAP["#ffffff"]
        return _MAP.get(m.group(0).lower(), m.group(0))
    return _RX.sub(sub, css)


def footer_bg():
    return "#334155" if _dark else "#e2e8f0"


def _in_sidebar(w):
    while w is not None:
        if w.objectName() == "sidebar":
            return True
        w = w.parentWidget()
    return False


def _convert(w):
    css = w.styleSheet()
    if not css or css == getattr(w, "_dark_css", None) or _in_sidebar(w):
        return
    w._light_css, w._dark_css = css, to_dark(css)
    w.setStyleSheet(w._dark_css)


class _Filter(QObject):
    def eventFilter(self, obj, ev):
        if _dark and ev.type() in (QEvent.Polish, QEvent.StyleChange) and isinstance(obj, QWidget):
            _convert(obj)
        return False


_filter = None


def install(app):
    """Call once after creating the QApplication."""
    global _filter, _original_style
    _original_style = app.style().objectName()
    _filter = _Filter(app)
    app.installEventFilter(_filter)


def _palette():
    p = QPalette()
    for role, col in ((QPalette.Window, DARK_PAGE), (QPalette.WindowText, "#e2e8f0"), (QPalette.Base, "#1e293b"),
                      (QPalette.AlternateBase, "#172033"), (QPalette.Text, "#e2e8f0"), (QPalette.Button, "#1e293b"),
                      (QPalette.ButtonText, "#e2e8f0"), (QPalette.ToolTipBase, "#1e293b"), (QPalette.ToolTipText, "#e2e8f0"),
                      (QPalette.Highlight, "#2563eb"), (QPalette.HighlightedText, "#ffffff"),
                      (QPalette.PlaceholderText, "#94a3b8")):
        p.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#64748b"))
    return p


def _light_palette():
    p = QPalette()
    for role, col in ((QPalette.Window, "#f1f5f9"), (QPalette.WindowText, "#0f172a"), (QPalette.Base, "#ffffff"),
                      (QPalette.AlternateBase, "#f8fafc"), (QPalette.Text, "#0f172a"), (QPalette.Button, "#ffffff"),
                      (QPalette.ButtonText, "#0f172a"), (QPalette.ToolTipBase, "#ffffff"), (QPalette.ToolTipText, "#0f172a"),
                      (QPalette.Highlight, "#2563eb"), (QPalette.HighlightedText, "#ffffff"),
                      (QPalette.PlaceholderText, "#94a3b8")):
        p.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#94a3b8"))
    return p


def set_dark(app, on):
    """Switch the whole application between light and dark. Both modes use the Fusion style with an explicit palette, so
    the result never depends on the Windows theme (a dark Windows would otherwise give light text on the light page)."""
    global _dark
    _dark = bool(on)
    try:                                                 # Qt 6.8+: stop Windows from forcing its own colour scheme
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if _dark else Qt.ColorScheme.Light)
    except (AttributeError, TypeError):
        pass
    app.setStyle("Fusion")
    if _dark:
        app.setPalette(_palette())
        app.setStyleSheet(STYLE.replace("#f1f5f9", DARK_PAGE))
        for w in app.allWidgets():
            _convert(w)
    else:
        for w in app.allWidgets():
            if getattr(w, "_dark_css", None) is not None:
                if w.styleSheet() == w._dark_css:
                    w.setStyleSheet(w._light_css)
                w._dark_css = w._light_css = None
        app.setPalette(_light_palette())
        app.setStyleSheet(STYLE)
    for w in app.allWidgets():                           # widgets created earlier keep no stale colours
        w.setPalette(QPalette())

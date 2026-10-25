"""Keyboard movement between fields, everywhere in the app.

Enter           -> next field (on the last field of a dialog, Enter works as before: Save / OK)
Up / Down       -> previous / next field in text boxes (number, date and drop-down boxes keep Up / Down for changing their value)
Left / Right    -> previous / next field when the cursor is already at the start / end of the text
A box can opt out (Enter keeps its own job, e.g. barcode scan, add item): widget.setProperty("enterAction", True)."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog, QLineEdit,
                               QPushButton, QWidget)

ENTER = (Qt.Key_Return, Qt.Key_Enter)


def field_of(w):
    """The input widget a key event belongs to (a spin box or combo's inner text box counts as the spin box / combo)."""
    if isinstance(w, QLineEdit):
        p = w.parentWidget()
        if isinstance(p, (QAbstractSpinBox, QComboBox)):
            return p
        return w
    if isinstance(w, (QAbstractSpinBox, QComboBox, QCheckBox)):
        return w
    return None


def _in_table(w):
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QAbstractItemView):
            return True
        p = p.parentWidget()
    return False


def _usable(c):
    if field_of(c) is not c or not c.isVisible() or not c.isEnabled() or not (c.focusPolicy() & Qt.TabFocus):
        return False
    if isinstance(c, QLineEdit) and c.isReadOnly():
        return False
    return not _in_table(c)


def neighbour(w, forward=True):
    """Next / previous input in the window's tab order, or None when the dialog's buttons (or the start) come first."""
    dialog = isinstance(w.window(), QDialog)
    c = w.nextInFocusChain() if forward else w.previousInFocusChain()
    for _ in range(400):
        if c is None or c is w:
            return None
        if _usable(c):
            return c
        if dialog and isinstance(c, QPushButton) and c.isVisible() and c.isEnabled():
            return None                                  # reached the Save / OK buttons: no more fields
        c = c.nextInFocusChain() if forward else c.previousInFocusChain()
    return None


class _Nav(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.KeyPress or ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        key = ev.key()
        if key not in ENTER + (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            return False
        w = field_of(obj)
        if w is None or w.property("enterAction") or _in_table(w):
            return False
        if isinstance(w, QComboBox) and w.view().isVisible():
            return False
        forward = key in ENTER + (Qt.Key_Down, Qt.Key_Right)
        if key in (Qt.Key_Up, Qt.Key_Down):
            if not isinstance(w, QLineEdit):             # spin boxes, dates and drop-downs use Up / Down for their value
                return False
        elif key in (Qt.Key_Left, Qt.Key_Right):
            if not isinstance(w, QLineEdit) or w.hasSelectedText():
                return False
            at_edge = w.cursorPosition() == (len(w.text()) if forward else 0)
            if not at_edge:
                return False
        elif ev.isAutoRepeat():
            return True
        target = neighbour(w, forward)
        if target is None:
            return False                                 # last field: Enter keeps its normal job (Save / OK)
        target.setFocus(Qt.TabFocusReason)
        return True


_filter = None


def install(app=None):
    global _filter
    app = app or QApplication.instance()
    _filter = _Nav(app)
    app.installEventFilter(_filter)

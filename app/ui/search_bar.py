"""Search box at the top of the window with a grouped dropdown of results."""
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .. import search


class SearchBar(QWidget):
    chosen = Signal(str, int)               # kind, id

    def __init__(self, con, host):
        super().__init__()
        self.con, self.host = con, host     # host: the window the dropdown floats over
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 10, 24, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search products, customers, suppliers, invoices, batches...")
        self.edit.setClearButtonEnabled(True)
        self.edit.setProperty("enterAction", True)
        self.edit.setStyleSheet("padding:9px 12px;border:1px solid #cbd5e1;border-radius:8px;background:white;font-size:14px;")
        v.addWidget(self.edit)

        self.pop = QListWidget(host)
        self.pop.setFrameShape(QFrame.StyledPanel)
        self.pop.setStyleSheet("QListWidget{background:white;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px;"
                               "font-size:14px;}QListWidget::item{padding:6px 12px;}"
                               "QListWidget::item:selected{background:#dbeafe;color:#0f172a;}")
        self.pop.hide()
        self.pop.itemActivated.connect(self.pick)
        self.pop.itemClicked.connect(self.pick)
        self.edit.installEventFilter(self)
        self.pop.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)                   # wait for a pause in typing
        self.timer.timeout.connect(self.run)
        self.edit.textChanged.connect(lambda _: self.timer.start())

    def focus(self):
        self.edit.setFocus()
        self.edit.selectAll()

    def run(self):
        groups = search.search_all(self.con, self.edit.text())
        self.pop.clear()
        if not self.edit.text().strip():
            return self.pop.hide()
        if not groups:
            it = QListWidgetItem("Nothing found")
            it.setFlags(Qt.NoItemFlags)
            self.pop.addItem(it)
        for title, rows in groups:
            head = QListWidgetItem(title.upper())
            head.setFlags(Qt.NoItemFlags)
            f = QFont(head.font())
            f.setBold(True)
            f.setPointSize(max(f.pointSize() - 1, 8))
            head.setFont(f)
            head.setForeground(QColor("#64748b"))
            self.pop.addItem(head)
            for main, sub, kind, rid in rows:
                it = QListWidgetItem(f"{main}\n{sub}" if sub else main)
                it.setData(Qt.UserRole, (kind, rid))
                self.pop.addItem(it)
        self.place()
        self.pop.show()
        self.pop.raise_()

    def place(self):
        p = self.edit.mapTo(self.host, self.edit.rect().bottomLeft())
        rows = self.pop.count()
        h = min(sum(self.pop.sizeHintForRow(i) for i in range(rows)) + 8, 420)
        self.pop.setGeometry(p.x(), p.y() + 4, self.edit.width(), h)

    def first_row(self):
        for i in range(self.pop.count()):
            if self.pop.item(i).data(Qt.UserRole):
                return i
        return -1

    def pick(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        self.pop.hide()
        self.chosen.emit(*data)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress:
            k = ev.key()
            if obj is self.edit:
                if k == Qt.Key_Escape:
                    self.pop.hide()
                    self.edit.clear()
                    return True
                if k in (Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter) and self.pop.isVisible():
                    i = self.first_row()
                    if i >= 0:
                        if k == Qt.Key_Down:
                            self.pop.setCurrentRow(i)
                            self.pop.setFocus()
                        else:
                            self.pick(self.pop.item(i))
                    return True
            elif obj is self.pop:
                if k == Qt.Key_Escape:
                    self.pop.hide()
                    self.edit.setFocus()
                    return True
                if k in (Qt.Key_Down, Qt.Key_Up):          # skip the group headings
                    step = 1 if k == Qt.Key_Down else -1
                    i = self.pop.currentRow() + step
                    while 0 <= i < self.pop.count() and not self.pop.item(i).data(Qt.UserRole):
                        i += step
                    if 0 <= i < self.pop.count():
                        self.pop.setCurrentRow(i)
                    elif k == Qt.Key_Up:
                        self.edit.setFocus()
                    return True
        elif ev.type() == QEvent.FocusOut and obj is self.edit and not self.pop.hasFocus():
            QTimer.singleShot(150, lambda: None if self.pop.hasFocus() or self.edit.hasFocus() else self.pop.hide())
        return False

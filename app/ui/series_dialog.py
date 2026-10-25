"""Invoice number series (RET-001, WHO-001, ...)."""
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import extras
from ..theme import BTN, BTN_GREY, INPUT, TABLE


class SeriesDialog(QDialog):
    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.con = con
        self.sid = None
        self.setWindowTitle("Invoice series")
        self.resize(640, 480)
        v = QVBoxLayout(self)
        note = QLabel("Each series numbers its bills separately, for example RET-001 for retail and WHO-001 for wholesale. "
                      "When billing you can pick the series; the default is used otherwise.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Prefix", "Digits", "Next bill no.", "Default"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setStyleSheet(TABLE)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.pick)
        v.addWidget(self.table, 1)
        f = QFormLayout()
        self.name, self.prefix = QLineEdit(), QLineEdit()
        self.digits, self.next = QSpinBox(), QSpinBox()
        self.digits.setRange(1, 10)
        self.digits.setValue(3)
        self.next.setRange(1, 999_999_999)
        self.default = QCheckBox("Use this series by default")
        for w in (self.name, self.prefix, self.digits, self.next):
            w.setStyleSheet(INPUT)
        self.prefix.setPlaceholderText("RET-")
        self.name.setPlaceholderText("Retail")
        self.example = QLabel()
        self.example.setStyleSheet("color:#2563eb;font-weight:600;")
        for w in (self.prefix, self.digits, self.next):
            (w.textChanged if isinstance(w, QLineEdit) else w.valueChanged).connect(self.show_example)
        f.addRow("Name", self.name)
        f.addRow("Prefix", self.prefix)
        f.addRow("Digits", self.digits)
        f.addRow("Next number", self.next)
        f.addRow("", self.default)
        f.addRow("Next bill will be", self.example)
        v.addLayout(f)
        row = QHBoxLayout()
        for text, fn, style in (("Save series", self.save, BTN), ("New series", self.new, BTN_GREY),
                                ("Delete", self.delete, BTN_GREY), ("Close", self.accept, BTN_GREY)):
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.load()
        self.new()

    def show_example(self, *_):
        p = self.prefix.text().strip().upper()
        self.example.setText(extras.example(p, self.digits.value(), self.next.value()) if p else "")

    def load(self):
        rows = extras.series_list(self.con)
        self.table.setRowCount(len(rows))
        self.ids = [r["id"] for r in rows]
        for i, r in enumerate(rows):
            for c, val in enumerate([r["name"], r["prefix"], r["digits"], r["next_no"], "Yes" if r["is_default"] else ""]):
                self.table.setItem(i, c, QTableWidgetItem(str(val)))

    def pick(self):
        r = self.table.currentRow()
        if not 0 <= r < len(self.ids):
            return
        s = next(x for x in extras.series_list(self.con) if x["id"] == self.ids[r])
        self.sid = s["id"]
        self.name.setText(s["name"])
        self.prefix.setText(s["prefix"])
        self.digits.setValue(s["digits"])
        self.next.setValue(s["next_no"])
        self.default.setChecked(bool(s["is_default"]))
        self.default.setEnabled(not s["is_default"])
        self.show_example()

    def new(self):
        self.table.clearSelection()
        self.sid = None
        self.name.clear()
        self.prefix.clear()
        self.digits.setValue(3)
        self.next.setValue(1)
        self.default.setChecked(False)
        self.default.setEnabled(True)
        self.show_example()

    def save(self):
        try:
            self.sid = extras.save_series(self.con, self.name.text(), self.prefix.text(), self.digits.value(), self.next.value(),
                                          self.default.isChecked(), self.sid)
        except ValueError as e:
            return QMessageBox.warning(self, "Invoice series", str(e))
        self.load()

    def delete(self):
        if not self.sid:
            return QMessageBox.information(self, "Invoice series", "Select a series first.")
        try:
            extras.delete_series(self.con, self.sid)
        except ValueError as e:
            return QMessageBox.warning(self, "Invoice series", str(e))
        self.load()
        self.new()

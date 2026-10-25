"""Currencies and today's exchange rates."""
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import currency
from ..theme import BTN, BTN_GREY, INPUT, TABLE


class CurrencyDialog(QDialog):
    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.con = con
        self.setWindowTitle("Currencies")
        self.resize(620, 560)
        v = QVBoxLayout(self)
        self.base_note = QLabel()
        self.base_note.setWordWrap(True)
        v.addWidget(self.base_note)
        note = QLabel("Enter how much of your base currency one unit costs, e.g. 1 USD = 83.50. Update the rate whenever it "
                      "changes: bills and payments already made keep the rate they were made at.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Code", "Name", "Symbol", "Rate"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setStyleSheet(TABLE)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.pick)
        v.addWidget(self.table, 1)
        f = QFormLayout()
        self.code, self.name, self.sym = QLineEdit(), QLineEdit(), QLineEdit()
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(4)
        self.rate.setRange(0.0001, 1_000_000)
        self.rate.setValue(1)
        for w in (self.code, self.name, self.sym, self.rate):
            w.setStyleSheet(INPUT)
        self.code.setPlaceholderText("USD")
        self.code.setMaxLength(3)
        self.name.setPlaceholderText("US Dollar")
        self.sym.setPlaceholderText("$")
        f.addRow("Code", self.code)
        f.addRow("Name", self.name)
        f.addRow("Symbol", self.sym)
        self.rate_label = QLabel("Rate")
        f.addRow(self.rate_label, self.rate)
        v.addLayout(f)
        row = QHBoxLayout()
        for text, fn, style in (("Save currency", self.save, BTN), ("New", self.new, BTN_GREY), ("Delete", self.delete, BTN_GREY)):
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("Base currency"))
        self.base_box = QComboBox()
        self.base_box.setStyleSheet(INPUT)
        brow.addWidget(self.base_box)
        self.base_btn = QPushButton("Change base currency")
        self.base_btn.setStyleSheet(BTN_GREY)
        self.base_btn.clicked.connect(self.change_base)
        brow.addWidget(self.base_btn)
        brow.addStretch()
        close = QPushButton("Close")
        close.setStyleSheet(BTN)
        close.clicked.connect(self.accept)
        brow.addWidget(close)
        v.addLayout(brow)
        self.load()
        self.new()

    def load(self):
        rows = currency.listing(self.con)
        base = currency.base_code(self.con)
        self.codes = [r["code"] for r in rows]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for c, val in enumerate([r["code"] + ("  (base)" if r["code"] == base else ""), r["name"], r["symbol"], f"{r['rate']:g}"]):
                self.table.setItem(i, c, QTableWidgetItem(val))
        self.base_note.setText(f"<b>Base currency: {base}.</b> Everything - stock value, GST, reports - is kept in {base}.")
        self.base_box.clear()
        self.base_box.addItems(self.codes)
        self.base_box.setCurrentText(base)
        self.base_btn.setEnabled(not currency.has_transactions(self.con))
        self.base_btn.setToolTip("" if self.base_btn.isEnabled() else "Not possible once bills, purchases, payments or expenses exist.")

    def pick(self):
        r = self.table.currentRow()
        if not 0 <= r < len(self.codes):
            return
        row = next(x for x in currency.listing(self.con) if x["code"] == self.codes[r])
        self.code.setText(row["code"])
        self.code.setEnabled(False)
        self.name.setText(row["name"])
        self.sym.setText(row["symbol"])
        is_base = row["code"] == currency.base_code(self.con)
        self.rate.setValue(row["rate"])
        self.rate.setEnabled(not is_base)

    def new(self):
        self.table.clearSelection()
        self.code.setEnabled(True)
        self.rate.setEnabled(True)
        for w in (self.code, self.name, self.sym):
            w.clear()
        self.rate.setValue(1)

    def save(self):
        try:
            currency.save_currency(self.con, self.code.text(), self.name.text(), self.sym.text(), self.rate.value())
        except ValueError as e:
            return QMessageBox.warning(self, "Currencies", str(e))
        self.load()
        self.new()

    def delete(self):
        r = self.table.currentRow()
        if not 0 <= r < len(self.codes):
            return QMessageBox.information(self, "Currencies", "Select a currency first.")
        try:
            currency.delete_currency(self.con, self.codes[r])
        except ValueError as e:
            return QMessageBox.warning(self, "Currencies", str(e))
        self.load()
        self.new()

    def change_base(self):
        try:
            currency.set_base(self.con, self.base_box.currentText())
        except ValueError as e:
            return QMessageBox.warning(self, "Currencies", str(e))
        QMessageBox.information(self, "Currencies", "Base currency changed. Check the rates of the other currencies: they now mean "
                                "'how much of the new base currency one unit costs'.")
        self.load()

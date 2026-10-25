"""Expenses: rent, salaries, electricity and so on. They feed the cash / bank books and the Profit & Loss statement."""
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import expenses
from ..theme import BTN, BTN_GREY, INPUT, TABLE


class ExpensesPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Expenses")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        sub = QLabel("Shop spending that is not stock: rent, salaries, electricity, transport. Paying GST to the government "
                     "is entered here too (category \"GST paid to government\").")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#64748b;")
        v.addWidget(sub)

        form = QHBoxLayout()
        self.on = QDateEdit(QDate.currentDate())
        self.on.setCalendarPopup(True)
        self.on.setDisplayFormat("dd MMM yyyy")
        self.cat = QComboBox()
        self.cat.setEditable(True)
        self.cat.addItems(expenses.CATEGORIES)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 99_999_999)
        self.amount.setDecimals(2)
        self.amount.setPrefix("₹ ")
        self.mode = QComboBox()
        self.mode.addItems(expenses.MODES)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Note (optional)")
        self.note.setProperty("enterAction", True)
        self.note.returnPressed.connect(self.add)
        add = QPushButton("Add expense")
        add.setStyleSheet(BTN)
        add.clicked.connect(self.add)
        for w in (self.on, self.cat, self.amount, self.mode, self.note):
            w.setStyleSheet(INPUT)
            form.addWidget(w)
        form.addWidget(add)
        v.addLayout(form)

        bar = QHBoxLayout()
        today = QDate.currentDate()
        self.d1, self.d2 = QDateEdit(QDate(today.year(), today.month(), 1)), QDateEdit(today)
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd MMM yyyy")
            d.setStyleSheet(INPUT)
            d.dateChanged.connect(self.refresh)
        self.total = QLabel()
        self.total.setStyleSheet("font-weight:700;")
        for w in (QLabel("From"), self.d1, QLabel("To"), self.d2):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.total)
        v.addLayout(bar)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Date", "Category", "Mode", "Note", "Amount"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        v.addWidget(self.table, 1)
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.setStyleSheet(BTN_GREY)
        self.delete_btn.clicked.connect(self.delete)
        row = QHBoxLayout()
        row.addWidget(self.delete_btn)
        row.addStretch()
        v.addLayout(row)
        self.refresh()

    def add(self):
        try:
            expenses.add(self.con, self.on.date().toString("yyyy-MM-dd"), self.cat.currentText().strip(), self.amount.value(),
                         self.mode.currentText(), self.note.text().strip())
        except ValueError as e:
            return QMessageBox.warning(self, "Could not add", str(e))
        self.amount.setValue(0)
        self.note.clear()
        self.refresh()

    def delete(self):
        r = self.table.currentRow()
        if not 0 <= r < len(self.rows):
            return QMessageBox.information(self, "Delete", "Select an expense first.")
        if QMessageBox.question(self, "Delete expense", "Delete this expense?") != QMessageBox.Yes:
            return
        try:
            expenses.delete(self.con, self.rows[r]["id"])
        except ValueError as e:
            return QMessageBox.warning(self, "Could not delete", str(e))
        self.refresh()

    def refresh(self, *_):
        self.rows = expenses.listing(self.con, self.d1.date().toString("yyyy-MM-dd"), self.d2.date().toString("yyyy-MM-dd"))
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            vals = ["🔒" if r["frozen"] else "", r["spent_on"][:10], r["category"], r["mode"], r["note"] or "", f"{r['amount']:,.2f}"]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 5:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.total.setText(f"Total: ₹ {sum(r['amount'] for r in self.rows):,.2f}")

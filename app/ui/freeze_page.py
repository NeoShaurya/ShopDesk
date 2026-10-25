"""Freeze Entries: lock finished bills, purchases, returns and payments so they can never be changed."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import freeze
from ..theme import BTN, BTN_GREY, INPUT, TABLE


class FreezePage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Freeze Entries")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        sub = QLabel("A frozen entry 🔒 can never be edited or deleted, and freezing cannot be undone. "
                     "Freeze a month's entries once you have checked them.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#64748b;")
        v.addWidget(sub)

        bar = QHBoxLayout()
        self.kind, self.state = QComboBox(), QComboBox()
        for k, (_, label, *_r) in freeze.KINDS.items():
            self.kind.addItem(label, k)
        for label, val in (("Not frozen yet", "open"), ("Frozen", "frozen"), ("All", None)):
            self.state.addItem(label, val)
        today = QDate.currentDate()
        self.d1, self.d2 = QDateEdit(QDate(today.year(), today.month(), 1)), QDateEdit(today)
        for w in (self.kind, self.state):
            w.setStyleSheet(INPUT)
            w.currentIndexChanged.connect(self.refresh)
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd MMM yyyy")
            d.setStyleSheet(INPUT)
            d.dateChanged.connect(self.refresh)
        for w in (self.kind, self.state, QLabel("From"), self.d1, QLabel("To"), self.d2):
            bar.addWidget(w)
        bar.addStretch()
        v.addLayout(bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Date", "Reference", "Party", "Amount"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        sel = QPushButton("Freeze selected")
        sel.setStyleSheet(BTN)
        sel.clicked.connect(self.freeze_selected)
        row.addWidget(sel)
        row.addSpacing(24)
        row.addWidget(QLabel("Freeze everything up to"))
        self.upto = QDateEdit(QDate(today.year(), today.month(), 1).addDays(-1))
        self.upto.setCalendarPopup(True)
        self.upto.setDisplayFormat("dd MMM yyyy")
        self.upto.setStyleSheet(INPUT)
        row.addWidget(self.upto)
        allb = QPushButton("Freeze all")
        allb.setStyleSheet(BTN_GREY)
        allb.clicked.connect(self.freeze_all)
        row.addWidget(allb)
        row.addStretch()
        self.count = QLabel()
        self.count.setStyleSheet("color:#64748b;")
        row.addWidget(self.count)
        v.addLayout(row)
        self.refresh()

    def refresh(self, *_):
        self.rows = freeze.entries(self.con, self.kind.currentData(), self.d1.date().toString("yyyy-MM-dd"),
                                   self.d2.date().toString("yyyy-MM-dd"), self.state.currentData())
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            cells = ["🔒" if r["frozen"] else "", QDate.fromString(r["day"], "yyyy-MM-dd").toString("dd MMM yyyy"),
                     r["ref"], r["party"] or "", f"{r['amount']:,.2f}"]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.count.setText(f"{len(self.rows)} entries")

    def freeze_selected(self):
        ids = sorted({self.rows[i.row()]["id"] for i in self.table.selectedIndexes()})
        if not ids:
            return QMessageBox.information(self, "Freeze", "Select the entries to freeze first.")
        if QMessageBox.question(self, "Freeze entries", f"Freeze {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}?\n\n"
                                "They can never be edited or deleted after this.") != QMessageBox.Yes:
            return
        n = freeze.freeze_ids(self.con, self.kind.currentData(), ids)
        QMessageBox.information(self, "Frozen", f"{n} entr{'y' if n == 1 else 'ies'} frozen.")
        self.refresh()

    def freeze_all(self):
        upto = self.upto.date().toString("yyyy-MM-dd")
        if QMessageBox.question(self, "Freeze everything", f"Freeze ALL bills, purchases, returns and payments dated "
                                f"up to {self.upto.date().toString('dd MMM yyyy')}?\n\nThey can never be edited or deleted "
                                "after this. Take a backup first if you are unsure.") != QMessageBox.Yes:
            return
        out = freeze.freeze_upto(self.con, upto)
        QMessageBox.information(self, "Frozen", f"{sum(out.values())} entries frozen.")
        self.refresh()

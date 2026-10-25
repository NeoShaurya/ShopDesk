"""Financial Years: see the years, close a finished year (read-only), start the next one."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import fy
from ..theme import BTN, BTN_GREY, TABLE


class FyPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Financial Years")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        sub = QLabel("A financial year runs from 1 April to 31 March. A closed year is read-only: its bills, purchases, "
                     "returns and payments can no longer be added to, changed or deleted. Balances carry forward to the next year by themselves.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#64748b;")
        v.addWidget(sub)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Year", "From", "To", "Status", "Bills", "Purchases"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        self.table.itemSelectionChanged.connect(self.buttons)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.b_close, self.b_open, self.b_next = QPushButton("Close this year"), QPushButton("Reopen"), QPushButton("Start next year")
        self.b_close.setStyleSheet(BTN)
        self.b_open.setStyleSheet(BTN_GREY)
        self.b_next.setStyleSheet(BTN_GREY)
        self.b_close.clicked.connect(self.close_year)
        self.b_open.clicked.connect(self.reopen_year)
        self.b_next.clicked.connect(self.next_year)
        for b in (self.b_close, self.b_open, self.b_next):
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.refresh()

    def refresh(self, *_):
        self.rows = fy.listing(self.con)
        self.table.setRowCount(len(self.rows))
        cur = fy.current(self.con)["id"]
        for i, y in enumerate(self.rows):
            n_s = self.con.execute("SELECT COUNT(*) FROM sales WHERE financial_year_id=?", (y["id"],)).fetchone()[0]
            n_p = self.con.execute("SELECT COUNT(*) FROM purchases WHERE financial_year_id=?", (y["id"],)).fetchone()[0]
            cells = [y["label"] + ("  (current)" if y["id"] == cur else ""),
                     QDate.fromString(y["start_date"], "yyyy-MM-dd").toString("dd MMM yyyy"),
                     QDate.fromString(y["end_date"], "yyyy-MM-dd").toString("dd MMM yyyy"),
                     "Closed 🔒" if y["status"] == "closed" else "Open", str(n_s), str(n_p)]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c >= 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        if self.rows:
            self.table.selectRow(0)
        self.buttons()

    def current(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def buttons(self):
        y = self.current()
        self.b_close.setEnabled(bool(y) and y["status"] == "open")
        self.b_open.setEnabled(bool(y) and y["status"] == "closed")

    def close_year(self):
        y = self.current()
        if not y:
            return
        if QMessageBox.question(self, "Close financial year",
                                f"Close {y['label']}?\n\nIts records become read-only. Closing balances carry forward "
                                "to the next year automatically. Take a backup first.") != QMessageBox.Yes:
            return
        try:
            fy.close(self.con, y["id"])
        except ValueError as e:
            QMessageBox.warning(self, "Cannot close", str(e))
        self.refresh()

    def reopen_year(self):
        y = self.current()
        if y and QMessageBox.question(self, "Reopen financial year", f"Reopen {y['label']}? Its records can be changed again "
                                      "until you close it. This is recorded in the Audit Log.") == QMessageBox.Yes:
            fy.reopen(self.con, y["id"])
            self.refresh()

    def next_year(self):
        fy.new_next(self.con)
        self.refresh()

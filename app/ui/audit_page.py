"""Audit log viewer: read-only history of what was created, changed or deleted, and when."""
from datetime import datetime

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import audit
from ..export import ExcelButton
from ..theme import INPUT, TABLE

GROUPS = [("Everything", None), ("Products & batches", ["products", "batches"]),
          ("Customers & suppliers", ["parties"]), ("Bills", ["sales"]), ("Purchases", ["purchases"]),
          ("Returns", ["returns"]), ("Payments", ["payments"]), ("Stock corrections", ["stock_movements"]),
          ("Pricing & offers", ["price_lists", "price_list_items", "schemes"]), ("Settings", ["settings"]),
          ("Godowns", ["godowns"])]
ACTIONS = [("Any action", None), ("Created", "create"), ("Changed", "update"), ("Deleted", "delete"),
           ("Stock adjusted", "stock adjust")]
SHOWN = {"create": "Created", "update": "Changed", "delete": "Deleted", "stock adjust": "Stock adjusted"}
HEADERS = ["When", "What", "Record", "Action", "Before", "After"]


class AuditPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Audit Log")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        sub = QLabel("A permanent record of changes. Entries cannot be edited or deleted.")
        sub.setStyleSheet("color:#64748b;")
        v.addWidget(sub)

        bar = QHBoxLayout()
        self.group, self.action = QComboBox(), QComboBox()
        for box, items in ((self.group, GROUPS), (self.action, ACTIONS)):
            box.setStyleSheet(INPUT)
            for label, val in items:
                box.addItem(label, val)
            box.currentIndexChanged.connect(self.refresh)
        today = QDate.currentDate()
        self.d1, self.d2 = QDateEdit(today.addDays(-30)), QDateEdit(today)
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd MMM yyyy")
            d.setStyleSheet(INPUT)
            d.dateChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, invoice, value...")
        self.search.setStyleSheet(INPUT)
        self.search.textChanged.connect(self.refresh)
        for w in (self.group, self.action, QLabel("From"), self.d1, QLabel("To"), self.d2):
            bar.addWidget(w)
        bar.addWidget(self.search, 1)
        bar.addWidget(ExcelButton(self.get_reps, "Audit_log"))
        v.addLayout(bar)

        self.table = QTableWidget()
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setStyleSheet(TABLE)
        v.addWidget(self.table, 1)
        self.count = QLabel()
        self.count.setStyleSheet("color:#64748b;")
        v.addWidget(self.count)
        self.refresh()

    def refresh(self, *_):
        rows = audit.entries(self.con, self.group.currentData(), self.action.currentData(),
                             self.d1.date().toString("yyyy-MM-dd"), self.d2.date().toString("yyyy-MM-dd"),
                             self.search.text())
        self.rows = [[datetime.fromisoformat(r["at"]).strftime("%d %b %Y %I:%M %p"),
                      audit.FRIENDLY.get(r["table_name"], r["table_name"]), r["note"] or "",
                      SHOWN.get(r["action"], r["action"]), audit.describe(r["old_value"]),
                      audit.describe(r["new_value"])] for r in rows]
        self.table.setRowCount(len(self.rows))
        for i, cells in enumerate(self.rows):
            for c, val in enumerate(cells):
                self.table.setItem(i, c, QTableWidgetItem(val))
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.resizeRowsToContents()
        self.count.setText(f"{len(self.rows)} entries" + (" (showing the latest 2,000)" if len(self.rows) >= 2000 else ""))

    def get_reps(self):
        self.refresh()
        return [{"title": "Audit log", "subtitle": f"{self.d1.date().toString('dd MMM yyyy')} to "
                 f"{self.d2.date().toString('dd MMM yyyy')}", "headers": HEADERS, "aligns": "l" * len(HEADERS),
                 "rows": self.rows, "footer": []}]

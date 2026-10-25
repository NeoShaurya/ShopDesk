"""Reports screen: pick a report, see it on screen, Print / Preview / Save PDF."""
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import books, db, fy, printing, reports
from ..export import ExcelButton
from .. import theme
from ..theme import INPUT, TABLE
from .invoice_dialog import InvoiceDialog


class Reports(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rep = None
        self._party_kind = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Reports")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        bar = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.setStyleSheet(INPUT)
        for label, key in [("Sales report", "sales"), ("Profit & Loss", "pl"), ("Stock report", "stock"),
                           ("Expiry report", "expiry"), ("Reorder suggestion", "reorder"), ("Purchase register", "purchases"),
                           ("Cancelled bills", "cancelled"), ("Foreign currency transactions", "currency"), ("Day book", "daybook"), ("Cash book", "cashbook"), ("Bank book", "bankbook"),
                           ("Customer ledger statement", "cust_ledger"), ("Supplier ledger statement", "sup_ledger"),
                           ("Outstanding receivables", "receivables"), ("Outstanding payables", "payables"),
                           ("Stock valuation", "valuation"), ("Batch-wise stock valuation", "batch_valuation"),
                           ("Profit & Loss statement", "pnl"), ("Balance sheet", "balance"),
                           ("Sales tax (GST) summary", "gstsum"), ("Item-wise sales", "item_sales"),
                           ("Item-wise purchases", "item_purchases"),
                           ("PDC register", "pdc"), ("Pending PDCs", "pdc_pending"), ("Bounced cheques", "pdc_bounced")]:
            self.kind.addItem(label, key)
        today = QDate.currentDate()
        self.d1 = QDateEdit(QDate(today.year(), today.month(), 1))
        self.d2 = QDateEdit(today)
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd MMM yyyy")
            d.setStyleSheet(INPUT)
            d.dateChanged.connect(self.refresh)
        self.days = QComboBox()
        self.days.setStyleSheet(INPUT)
        for n in (7, 15, 30, 60, 90):
            self.days.addItem(f"Within {n} days", n)
        self.days.setCurrentIndex(2)
        self.days.currentIndexChanged.connect(self.refresh)
        self.cover = QComboBox()
        self.cover.setStyleSheet(INPUT)
        for n in (15, 30, 45, 60):
            self.cover.addItem(f"Cover {n} days of sales", n)
        self.cover.setCurrentIndex(1)
        self.cover.currentIndexChanged.connect(self.refresh)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.year = QComboBox()
        self.year.setStyleSheet(INPUT)
        self.year.currentIndexChanged.connect(self.pick_year)
        self.party = QComboBox()
        self.party.setStyleSheet(INPUT)
        self.party.setMinimumWidth(180)
        self.party.currentIndexChanged.connect(self.refresh)
        self.lbl_from, self.lbl_to = QLabel("From"), QLabel("To")
        for w in (self.kind, self.party, self.year, self.lbl_from, self.d1, self.lbl_to, self.d2, self.days, self.cover):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(printing.PrintBar(self.get_doc))
        bar.addWidget(ExcelButton(self.get_reps, "Report"))
        v.addLayout(bar)

        self.sub = QLabel()
        self.sub.setStyleSheet("color:#64748b;")
        v.addWidget(self.sub)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        self.table.doubleClicked.connect(self.open_bill)
        v.addWidget(self.table, 1)
        self.hint = QLabel()
        self.hint.setStyleSheet("color:#64748b;")
        v.addWidget(self.hint)
        self.load_years()
        self.refresh()

    def load_years(self):
        self.year.blockSignals(True)
        self.year.clear()
        self.year.addItem("Custom dates", None)
        for y in fy.listing(self.con):
            self.year.addItem(f"Financial year {y['label']}", (y["start_date"], y["end_date"]))
        self.year.blockSignals(False)

    def load_parties(self, key):
        self._party_kind = key
        self.party.blockSignals(True)
        self.party.clear()
        self.party.addItem("Choose a party...", None)
        kind = "customer" if key == "cust_ledger" else "supplier"
        for p in self.con.execute("SELECT id, name FROM parties WHERE type = ? AND name != 'Walk-in Customer' ORDER BY name", (kind,)):
            self.party.addItem(p["name"], p["id"])
        self.party.blockSignals(False)

    def pick_year(self, *_):
        span = self.year.currentData()
        if span:
            for d, iso in ((self.d1, span[0]), (self.d2, span[1])):
                d.blockSignals(True)
                d.setDate(QDate.fromString(iso, "yyyy-MM-dd"))
                d.blockSignals(False)
        self.refresh()

    def build(self):
        key = self.kind.currentData()
        a, b = self.d1.date().toString("yyyy-MM-dd"), self.d2.date().toString("yyyy-MM-dd")
        if key == "sales":
            return reports.sales_report(self.con, a, b)
        if key == "pl":
            return reports.profit_report(self.con, a, b)
        if key == "purchases":
            return reports.purchases_report(self.con, a, b)
        books_dates = {"daybook": books.day_book, "cashbook": books.cash_book, "bankbook": books.bank_book,
                       "pnl": books.profit_loss, "gstsum": books.gst_summary, "item_sales": books.item_sales,
                       "item_purchases": books.item_purchases}
        if key in books_dates:
            return books_dates[key](self.con, a, b)
        if key in ("receivables", "payables"):
            return books.outstanding(self.con, "customer" if key == "receivables" else "supplier")
        if key == "valuation":
            return books.stock_valuation(self.con, b)
        if key == "batch_valuation":
            return books.batch_valuation(self.con, b)
        if key == "balance":
            return books.balance_sheet(self.con, b)
        if key in ("cust_ledger", "sup_ledger"):
            pid = self.party.currentData()
            if pid is None:
                return {"title": "Ledger Statement", "subtitle": "Choose a party above", "headers": ["Date", "Details", "Debit", "Credit", "Balance"],
                        "aligns": "llrrr", "rows": [], "footer": [], "ids": []}
            return reports.ledger_report(self.con, pid, a, b)
        if key == "currency":
            return reports.currency_report(self.con, a, b)
        if key == "cancelled":
            return reports.cancelled_bills(self.con, a, b)
        if key == "pdc":
            return reports.pdc_register(self.con, a, b)
        if key == "pdc_pending":
            return reports.pdc_pending(self.con)
        if key == "pdc_bounced":
            return reports.pdc_bounced(self.con)
        if key == "expiry":
            return reports.expiry_report(self.con, self.days.currentData())
        if key == "reorder":
            return reports.reorder_report(self.con, self.cover.currentData())
        return reports.stock_report(self.con)

    def refresh(self, *_):
        key = self.kind.currentData()
        ledger = key in ("cust_ledger", "sup_ledger")
        if ledger and self._party_kind != key:
            self.load_parties(key)
        dates = key in ("currency", "cancelled", "sales", "pl", "purchases", "pdc", "daybook", "cashbook", "bankbook", "pnl", "gstsum", "item_sales",
                        "item_purchases", "valuation", "batch_valuation", "balance") or ledger
        self.party.setVisible(ledger)
        for w in (self.lbl_from, self.d1, self.lbl_to, self.d2):
            w.setVisible(dates)
        self.year.setVisible(dates)
        self.days.setVisible(key == "expiry")
        self.cover.setVisible(key == "reorder")
        self.rep = rep = self.build()
        self.sub.setText(f"{rep['title']}  -  {rep['subtitle']}")
        self.hint.setText("Double-click a bill to open and reprint it." if key == "sales" else "")
        rows = rep["rows"] + rep["footer"]
        self.table.clear()
        self.table.setColumnCount(len(rep["headers"]))
        self.table.setHorizontalHeaderLabels(rep["headers"])
        self.table.setRowCount(len(rows))
        for r, cells in enumerate(rows):
            foot = r >= len(rep["rows"])
            for c, val in enumerate(cells):
                it = QTableWidgetItem(str(val))
                if rep["aligns"][c] == "r":
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if foot:
                    f = QFont(it.font())
                    f.setBold(True)
                    it.setFont(f)
                    it.setBackground(QBrush(QColor(theme.footer_bg())))
                self.table.setItem(r, c, it)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.Stretch)

    def open_bill(self, index):
        ids = self.rep.get("ids") or []
        if 0 <= index.row() < len(ids):
            InvoiceDialog(self.con, ids[index.row()], self).exec()

    def get_reps(self):
        self.refresh()
        return [self.rep]

    def get_doc(self):
        """Called by the Print buttons: always prints exactly what is on screen."""
        self.refresh()
        shop = db.get_settings(self.con)
        name = f"{self.rep['title'].replace(' ', '_').replace('&', 'and')}_{date.today():%Y-%m-%d}"
        return printing.report_html(self.rep, shop), "a4", name

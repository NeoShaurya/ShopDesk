"""Returns: customer returns (credit note) and supplier returns (debit note), plus a list of past notes."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTabWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import db
from ..theme import BTN, BTN_GREY, INPUT
from .invoice_dialog import NoteDialog
from .payment import rupees
from .stock import col, make_table


def nice_date(s):
    return date.fromisoformat(s[:10]).strftime("%d %b %Y")


class ReturnTab(QWidget):
    def __init__(self, con, kind):
        super().__init__()
        self.con, self.kind = con, kind
        self.cust = kind == "customer"
        self.docs, self.lines, self.spins, self.doc = {}, [], [], None
        v = QVBoxLayout(self)
        v.setSpacing(10)

        row = QHBoxLayout()
        self.pick = QComboBox()
        self.pick.setEditable(True)
        self.pick.setInsertPolicy(QComboBox.NoInsert)
        self.pick.setStyleSheet(INPUT)
        self.pick.lineEdit().setPlaceholderText("Type the invoice number or customer name..." if self.cust
                                                else "Type the purchase invoice number or supplier name...")
        self.pick.setProperty("enterAction", True)
        self.pick.lineEdit().returnPressed.connect(self.load)
        self.pick.activated.connect(lambda _i: self.load())
        go = QPushButton("Load")
        go.setStyleSheet(BTN)
        go.clicked.connect(self.load)
        row.addLayout(col("Original " + ("invoice" if self.cust else "purchase"), self.pick), 1)
        row.addWidget(go, 0, Qt.AlignBottom)
        v.addLayout(row)

        self.info = QLabel()
        self.info.setStyleSheet("color:#475569;font-size:14px;")
        v.addWidget(self.info)

        self.table = make_table(["Product", "Batch", "Sold" if self.cust else "Bought", "Returned before",
                                 "Can return", "Rate", "GST %", "Return qty", "Credit" if self.cust else "Debit"])
        v.addWidget(self.table, 1)

        opts = QHBoxLayout()
        self.reason = QComboBox()
        self.reason.setStyleSheet(INPUT)
        if self.cust:
            self.reason.setEditable(True)
            self.reason.addItems(["Damaged", "Wrong item supplied", "Expired", "Customer changed mind"])
        else:
            self.reason.addItems(["Damaged", "Expired"])
        self.settle = QComboBox()
        self.settle.setStyleSheet(INPUT)
        words = ("Refund to customer by", "Adjust against customer's balance") if self.cust else \
                ("Refund received from supplier by", "Adjust against what I owe the supplier")
        self.settle.addItem(words[1], "adjust")
        for m in ("Cash", "UPI", "Card"):
            self.settle.addItem(f"{words[0]} {m}", m.lower())
        self.restock = QCheckBox("Put the goods back into stock")
        self.restock.setChecked(True)
        self.restock.setVisible(self.cust)
        opts.addLayout(col("Reason", self.reason), 1)
        opts.addLayout(col("Settlement", self.settle), 2)
        opts.addWidget(self.restock, 0, Qt.AlignBottom)
        v.addLayout(opts)

        bottom = QHBoxLayout()
        self.totals = QLabel()
        self.totals.setStyleSheet("font-size:16px;font-weight:600;color:#0f172a;")
        make = QPushButton("Create credit note" if self.cust else "Create debit note")
        make.setStyleSheet(BTN)
        make.clicked.connect(self.create)
        bottom.addWidget(self.totals, 1)
        bottom.addWidget(make)
        v.addLayout(bottom)
        self.recalc()

    def refresh(self):
        rows = db.recent_sales(self.con) if self.cust else db.recent_purchases(self.con)
        self.docs = {f"{r['ref']}  -  {r['party_name']}  -  ₹{r['total']:,.2f}  -  {nice_date(r['doc_date'])}": r
                     for r in rows}
        self.pick.clear()
        self.pick.addItems(list(self.docs))
        self.pick.setCurrentIndex(-1)
        comp = QCompleter(list(self.docs), self.pick)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.activated.connect(lambda _t: self.load())
        self.pick.setCompleter(comp)

    def resolve(self):
        t = self.pick.currentText().strip().lower()
        hits = [r for k, r in self.docs.items() if k.lower() == t] or [r for k, r in self.docs.items() if t and t in k.lower()]
        if len(hits) == 1:
            return hits[0]
        QMessageBox.information(self, "Find document", "Nothing matches that." if not hits
                                else f"{len(hits)} documents match - type more of the number or name.")
        return None

    def load(self, keep_pick=False):
        doc = self.doc if keep_pick else self.resolve()
        if not doc:
            return
        self.doc = doc
        self.lines = db.return_lines(self.con, self.kind, doc["id"])
        self.info.setText(f"{doc['ref']}   |   {doc['party_name']}   |   total ₹{doc['total']:,.2f}   |   "
                          f"{nice_date(doc['doc_date'])}" + ("" if self.lines else
                          "   |   No item details are saved for this document, so nothing can be returned from it."))
        for r in range(self.table.rowCount()):          # remove old row widgets so none are left drawn
            w = self.table.cellWidget(r, 7)
            if w:
                w.hide()
                self.table.removeCellWidget(r, 7)
        self.table.setRowCount(len(self.lines))
        self.spins = []
        for i, l in enumerate(self.lines):
            cells = [l["name"], l["batch_no"], l["qty"], l["returned"], l["can_return"], f"{l['rate']:,.2f}",
                     f"{l['gst_percent']:g}", None, "0.00"]
            for c, val in enumerate(cells):
                if val is None:
                    continue
                it = QTableWidgetItem(str(val))
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
            sp = QSpinBox()
            sp.setRange(0, l["can_return"])
            sp.setEnabled(l["can_return"] > 0)
            sp.valueChanged.connect(self.recalc)
            self.table.setCellWidget(i, 7, sp)
            self.spins.append(sp)
        self.recalc()

    def ratio(self):
        return db.discount_ratio(self.doc) if (self.doc and self.cust) else 0.0

    def recalc(self, *_):
        tv = ga = 0.0
        for i, (l, sp) in enumerate(zip(self.lines, self.spins)):
            t, g = db.return_amounts(l, sp.value(), self.ratio()) if sp.value() else (0.0, 0.0)
            tv, ga = tv + t, ga + g
            it = self.table.item(i, 8)
            if it:
                it.setText(f"{t + g:,.2f}")
        self.totals.setText(f"Taxable {rupees(tv)}    GST {rupees(ga)}    Total {rupees(tv + ga)}")

    def create(self):
        if not self.doc:
            return QMessageBox.information(self, "Returns", "Load an original document first.")
        items = [{"ref_item_id": l["id"], "qty": sp.value()} for l, sp in zip(self.lines, self.spins) if sp.value() > 0]
        if not items:
            return QMessageBox.information(self, "Returns", "Set a return quantity for at least one item.")
        method = self.settle.currentData()
        if method == "adjust" and (self.doc["party_name"].startswith("Walk-in") or not self.doc["party_id"]):
            return QMessageBox.warning(self, "Settlement", "A walk-in customer has no account to adjust.\n"
                                       "Choose a refund method (Cash, UPI or Card).")
        try:
            nid = db.create_return(self.con, self.kind, self.doc["id"], items, self.reason.currentText().strip(),
                                   self.restock.isChecked(), method)
        except ValueError as e:
            return QMessageBox.warning(self, "Cannot create note", str(e))
        NoteDialog(self.con, nid, self).exec()
        self.load(keep_pick=True)


class NotesTab(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setStyleSheet(INPUT)
        self.search.setPlaceholderText("Search note number, invoice or party...")
        self.search.textChanged.connect(self.refresh)
        v.addWidget(self.search)
        self.table = make_table(["Note", "Type", "Date", "Against", "Party", "Settled by", "Total"])
        self.table.horizontalHeader().setSectionResizeMode(4, self.table.horizontalHeader().ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(self.open)
        v.addWidget(self.table, 1)
        hint = QLabel("Double-click a note to open, print or save it as PDF.")
        hint.setStyleSheet("color:#64748b;")
        v.addWidget(hint)

    def refresh(self, *_):
        self.rows = db.list_notes(self.con, self.search.text().strip())
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            cells = [r["note_no"], "Credit note" if r["kind"] == "customer" else "Debit note", nice_date(r["created_at"]),
                     r["ref_no"] or "", r["party_name"],
                     "Balance" if r["refund_method"] == "adjust" else r["refund_method"].upper(), f"{r['total']:,.2f}"]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 6:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)

    def open(self, index):
        if 0 <= index.row() < len(self.rows):
            NoteDialog(self.con, self.rows[index.row()]["id"], self).exec()


class ReturnsPage(QWidget):
    def __init__(self, con):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Returns")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab{padding:10px 18px;font-size:14px;}")
        self.parts = [ReturnTab(con, "customer"), ReturnTab(con, "supplier"), NotesTab(con)]
        for w, name in zip(self.parts, ["Customer return (credit note)", "Supplier return (debit note)",
                                        "Past credit / debit notes"]):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(lambda i: self.parts[i].refresh())
        v.addWidget(self.tabs, 1)
        self.refresh()

    def refresh(self):
        self.parts[self.tabs.currentIndex()].refresh()

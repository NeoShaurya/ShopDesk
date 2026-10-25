"""Customers & suppliers: contact details, GSTIN, and who owes what."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import currency, db, export, fy, printing, reports, share
from ..gst import clean_gstin, valid_gstin
from .cheque_dialog import ChequeDialog
from ..gstr import STATES
from .. import theme
from ..theme import BTN, BTN_GREY, INPUT, TABLE


class PartyDialog(QDialog):
    def __init__(self, ptype, party=None, parent=None, con=None):
        super().__init__(parent)
        self.setWindowTitle(("Edit " if party else "New ") + ptype)
        self.setMinimumWidth(400)
        g = lambda k: (party[k] or "") if party else ""
        self.name, self.phone, self.email = QLineEdit(g("name")), QLineEdit(g("phone")), QLineEdit(g("email"))
        self.address, self.gstin = QLineEdit(g("address")), QLineEdit(g("gstin"))
        self.gstin.setPlaceholderText("15 characters, e.g. 08ABCDE1234F1Z5 (optional)")
        f = QFormLayout(self)
        for label, w in [("Name *", self.name), ("Phone", self.phone), ("Email", self.email),
                         ("Address", self.address), ("GSTIN", self.gstin)]:
            f.addRow(label, w)
        self.state = QComboBox()
        self.state.addItem("Same as GSTIN / my state", None)
        for code, nm in STATES.items():
            self.state.addItem(f"{code} - {nm}", code)
        if party and party["state_code"]:
            self.state.setCurrentIndex(max(0, self.state.findData(party["state_code"])))
        f.addRow("State", self.state)
        self.plist = self.trade = self.credit = None
        if ptype == "customer" and con is not None:       # customer pricing terms
            self.plist = QComboBox()
            self.plist.addItem("Default (first price list)", None)
            for pl in db.list_price_lists(con):
                self.plist.addItem(pl["name"], pl["id"])
            if party and party["price_list_id"]:
                self.plist.setCurrentIndex(max(0, self.plist.findData(party["price_list_id"])))
            self.trade = QDoubleSpinBox()
            self.trade.setRange(0, 100)
            self.trade.setSuffix(" %")
            self.trade.setValue((party["trade_discount"] if party else 0) or 0)
            f.addRow("Price list", self.plist)
            f.addRow("Trade discount", self.trade)
            self.credit = QDoubleSpinBox()
            self.credit.setRange(0, 1_000_000_000)
            self.credit.setPrefix("₹ ")
            self.credit.setSpecialValueText("No limit")
            self.credit.setValue((party["credit_limit"] if party else 0) or 0)
            f.addRow("Credit limit", self.credit)
        self.cur = None
        if con is not None and currency.has_foreign(con):
            self.cur = QComboBox()
            for r in currency.listing(con):
                self.cur.addItem(f"{r['code']} - {r['name']}", r["code"])
            self.cur.setCurrentIndex(max(0, self.cur.findData((party["currency"] if party else None) or currency.base_code(con))))
            f.addRow("Usual currency", self.cur)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def accept(self):
        if not self.name.text().strip():
            return QMessageBox.warning(self, "Missing name", "Please enter a name.")
        g = clean_gstin(self.gstin.text())
        if g and not valid_gstin(g):
            if QMessageBox.question(self, "Check GSTIN", "This does not look like a valid 15-character GSTIN.\n"
                                    "Save anyway?") != QMessageBox.Yes:
                return
        super().accept()

    def data(self):
        return {"name": self.name.text().strip(), "phone": self.phone.text().strip(), "email": self.email.text().strip(),
                "address": self.address.text().strip(), "gstin": clean_gstin(self.gstin.text()) or None,
                "state_code": self.state.currentData(),
                "price_list_id": self.plist.currentData() if self.plist else None,
                "trade_discount": self.trade.value() if self.trade else 0,
                "credit_limit": self.credit.value() if self.credit else 0,
                "currency": self.cur.currentData() if self.cur else None}


class PaymentDialog(QDialog):
    def __init__(self, party, ptype, parent=None, con=None):
        super().__init__(parent)
        self.con, self.party, self.rate = con, party, 1.0
        received = ptype == "customer"
        self.setWindowTitle("Payment received" if received else "Payment made")
        self.setMinimumWidth(340)
        f = QFormLayout(self)
        f.addRow(QLabel(f"<b>{party['name']}</b><br>Outstanding: ₹ {party['balance']:,.2f}"))
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, max(party["balance"], 0.01))
        self.amount.setPrefix("₹ ")
        self.amount.setValue(max(party["balance"], 0.01))
        self.cur = None
        if con is not None and currency.has_foreign(con):
            self.cur = QComboBox()
            for r in currency.listing(con):
                self.cur.addItem(r["code"], r["code"])
            self.cur.setCurrentIndex(max(0, self.cur.findData(party["currency"] or currency.base_code(con))))
            self.cur.currentIndexChanged.connect(self.cur_changed)
            self.equiv = QLabel()
            self.equiv.setStyleSheet("color:#64748b;")
            self.amount.valueChanged.connect(self.show_equiv)
        self.mode = QComboBox()
        for m in ("Cash", "UPI", "Card", "Cheque", "Bank transfer"):
            self.mode.addItem(m, m.lower())
        self.note = QLineEdit()
        if self.cur:
            f.addRow("Paid in", self.cur)
        f.addRow("Amount", self.amount)
        if self.cur:
            f.addRow("", self.equiv)
            self.cur_changed()
        f.addRow("Mode", self.mode)
        f.addRow("Note", self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def cur_changed(self, *_):
        """Amount is entered in the chosen currency; the box is capped at what is owed."""
        code = self.cur.currentData()
        base = currency.base_code(self.con)
        _, self.rate = currency.resolve(self.con, code)
        owed = max(self.party["balance"], 0.01)
        sym = "₹ " if code == "INR" else code + " "
        self.amount.setPrefix(sym)
        top = currency.from_base(owed, self.rate) if code != base else owed
        self.amount.setRange(0.01, max(top + (0.01 if code != base else 0), 0.01))
        self.amount.setValue(top)
        self.show_equiv()

    def show_equiv(self, *_):
        if not self.cur:
            return
        base = currency.base_code(self.con)
        self.equiv.setText("" if self.cur.currentData() == base else f"= {currency.show(base, self.base_amount())} at {self.rate:g}")

    def base_amount(self):
        """What the payment is worth in the base currency (the whole balance when the full amount is paid)."""
        if not self.cur or self.cur.currentData() == currency.base_code(self.con):
            return self.amount.value()
        if abs(self.amount.value() - currency.from_base(self.party["balance"], self.rate)) < 0.005:
            return round(self.party["balance"], 2)
        return min(currency.to_base(self.amount.value(), self.rate), round(self.party["balance"], 2))

    def currency_code(self):
        return self.cur.currentData() if self.cur else None


class PartiesPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.ptype = "customer"
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Customers & Suppliers")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        bar = QHBoxLayout()
        self.tab_c, self.tab_s = QPushButton("Customers"), QPushButton("Suppliers")
        for b, t in ((self.tab_c, "customer"), (self.tab_s, "supplier")):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setStyleSheet("QPushButton{background:white;border:1px solid #cbd5e1;padding:9px 18px;}"
                            "QPushButton:checked{background:#2563eb;color:white;border-color:#2563eb;font-weight:600;}")
            b.clicked.connect(lambda _=False, t=t: self.switch(t))
            bar.addWidget(b)
        self.tab_c.setChecked(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, phone or GSTIN...")
        self.search.setStyleSheet(INPUT)
        self.search.textChanged.connect(self.refresh)
        bar.addSpacing(12)
        bar.addWidget(self.search, 1)
        for text, fn, style in [("+ New", self.new, BTN), ("Edit", self.edit, BTN_GREY),
                                ("Ledger", self.ledger, BTN_GREY), ("Remind on WhatsApp", self.remind, BTN_GREY), ("Export Excel", self.export_excel, BTN_GREY), ("Record payment", self.pay, BTN_GREY), ("Delete", self.delete, BTN_GREY)]:
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            bar.addWidget(b)
        v.addLayout(bar)

        self.summary = QLabel()
        self.summary.setStyleSheet("font-size:15px;font-weight:600;")
        v.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Phone", "GSTIN", "Address", "Balance"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.edit)
        v.addWidget(self.table, 1)
        self.refresh()

    def switch(self, t):
        self.ptype = t
        self.refresh()

    def refresh(self, *_):
        rows = db.list_parties(self.con, self.ptype, self.search.text().strip())
        self.table.setRowCount(len(rows))
        cust = self.ptype == "customer"
        for i, r in enumerate(rows):
            bal = r["balance"]
            txt = "-" if abs(bal) < 0.005 else f"₹ {bal:,.2f}  " + ("to receive" if cust else "to pay")
            vals = [r["name"], r["phone"] or "", r["gstin"] or "", r["address"] or "", txt]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 0:
                    it.setData(Qt.UserRole, r["id"])
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if bal > 0.005:
                        it.setForeground(QColor("#dc2626" if cust else "#d97706"))
                self.table.setItem(i, c, it)
        total = sum(max(r["balance"], 0) for r in rows)
        self.summary.setText(f"Total {'to receive' if cust else 'to pay'}:  ₹ {total:,.2f}")
        self.summary.setStyleSheet(f"font-size:15px;font-weight:600;color:{'#dc2626' if total and cust else '#d97706' if total else '#16a34a'};")

    def selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select a row", "Click a row in the table first.")
            return None
        return self.table.item(row, 0).data(Qt.UserRole)

    def new(self):
        d = PartyDialog(self.ptype, parent=self, con=self.con)
        if d.exec():
            db.save_party(self.con, d.data(), None, self.ptype)
            self.refresh()

    def edit(self, *_):
        pid = self.selected()
        if pid is None:
            return
        d = PartyDialog(self.ptype, db.get_party(self.con, pid), self, self.con)
        if d.exec():
            db.save_party(self.con, d.data(), pid, self.ptype)
            self.refresh()

    def ledger(self):
        pid = self.selected()
        if pid is not None:
            LedgerDialog(self.con, pid, self).exec()

    def export_excel(self):
        export.save_dialog(self, [reports.parties_report(self.con, self.ptype)],
                           "Customers" if self.ptype == "customer" else "Suppliers")

    def remind(self):
        pid = self.selected()
        if pid is None:
            return
        p = db.get_party(self.con, pid)
        if p["balance"] <= 0.005:
            return QMessageBox.information(self, "Nothing due", f"{p['name']} has no outstanding balance.")
        shop = db.get_settings(self.con)
        phone = share.normalize_phone(p["phone"], shop["country_code"])
        if not phone:
            return QMessageBox.information(self, "WhatsApp", "Add this party's phone number first (Edit).")
        text = (f"Hello {p['name']}, a gentle reminder from {shop['shop_name']}: your outstanding balance is "
                f"Rs {p['balance']:,.2f}. Please arrange the payment. Thank you!") if self.ptype == "customer" else \
               f"Hello {p['name']}, this is {shop['shop_name']}. Our balance with you stands at Rs {p['balance']:,.2f}."
        share.open_whatsapp(phone, text)

    def pay(self):
        pid = self.selected()
        if pid is None:
            return
        p = db.get_party(self.con, pid)
        if p["balance"] <= 0.005:
            return QMessageBox.information(self, "Nothing due", f"{p['name']} has no outstanding balance.")
        d = PaymentDialog(p, self.ptype, self, self.con)
        if d.exec():
            try:
                pay_id = db.record_payment(self.con, pid, d.base_amount(), d.mode.currentData(), d.note.text().strip(),
                                           d.currency_code(), d.amount.value() if d.currency_code() else None)
            except ValueError as e:
                return QMessageBox.warning(self, "Payment", str(e))
            self.refresh()
            if d.mode.currentData() == "cheque" and self.ptype == "supplier" and QMessageBox.question(
                    self, "Print cheque", "Print the cheque for this payment now?") == QMessageBox.Yes:
                ChequeDialog(self.con, p["name"], d.base_amount(), pid, pay_id, parent=self).exec()

    def print_cheque(self):
        pid = self.selected()
        if pid is None:
            return
        p = db.get_party(self.con, pid)
        ChequeDialog(self.con, p["name"], max(p["balance"], 0), pid, parent=self).exec()

    def delete(self):
        pid = self.selected()
        if pid is None:
            return
        p = db.get_party(self.con, pid)
        if p["name"] == "Walk-in Customer":
            return QMessageBox.information(self, "Can't delete", "Walk-in Customer is used for cash sales.")
        if QMessageBox.question(self, "Delete", f"Delete {p['name']}?") != QMessageBox.Yes:
            return
        try:
            db.delete_party(self.con, pid)
        except ValueError as e:
            QMessageBox.information(self, "Can't delete", str(e))
        self.refresh()


class LedgerDialog(QDialog):
    """Statement of account for one customer or supplier, printable. Can show one financial year at a time."""

    def __init__(self, con, pid, parent=None):
        super().__init__(parent)
        self.con, self.pid = con, pid
        self.resize(860, 640)
        v = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:20px;font-weight:700;color:#0f172a;")
        v.addWidget(self.title)
        top = QHBoxLayout()
        self.sub = QLabel()
        self.sub.setStyleSheet("color:#64748b;")
        top.addWidget(self.sub, 1)
        self.year = QComboBox()
        self.year.setStyleSheet(INPUT)
        self.year.addItem("All time", None)
        for y in fy.listing(con):
            self.year.addItem(f"Financial year {y['label']}", (y["start_date"], y["end_date"]))
        self.year.currentIndexChanged.connect(self.fill)
        top.addWidget(self.year)
        v.addLayout(top)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(printing.PrintBar(lambda: (printing.report_html(self.rep, db.get_settings(con)), "a4", self.name())))
        row.addWidget(export.ExcelButton(lambda: [self.rep], "Ledger"))
        row.addStretch()
        close = QPushButton("Close")
        close.setStyleSheet(BTN)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self.fill()

    def name(self):
        return self.rep["title"].replace(" - ", "_").replace(" ", "_")

    def fill(self, *_):
        span = self.year.currentData()
        self.rep = rep = reports.ledger_report(self.con, self.pid, *(span if span else (None, None)))
        self.setWindowTitle(rep["title"])
        self.title.setText(rep["title"])
        self.sub.setText(rep["subtitle"])
        rows = rep["rows"] + rep["footer"]
        t = self.table
        t.clear()
        t.setColumnCount(len(rep["headers"]))
        t.setRowCount(len(rows))
        t.setHorizontalHeaderLabels(rep["headers"])
        for r, cells in enumerate(rows):
            foot = r >= len(rep["rows"])
            for c, val in enumerate(cells):
                it = QTableWidgetItem(str(val))
                if rep["aligns"][c] == "r":
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if foot:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setBackground(QColor(theme.footer_bg()))
                t.setItem(r, c, it)
        h = t.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)

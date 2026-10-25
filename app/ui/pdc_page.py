"""PDC Register: post-dated cheques received from customers and issued to suppliers."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import db, export, pdc
from ..theme import BTN, BTN_GREY, INPUT, TABLE
from .cheque_dialog import ChequeDialog

COLS = ["Cheque date", "Party", "Type", "Cheque no.", "Bank", "Amount", "Status", "When"]


def when_text(r):
    if r["status"] in ("cleared", "bounced"):
        return ""
    d = r["days_left"]
    return "Today" if d == 0 else f"in {d} day(s)" if d > 0 else f"{-d} day(s) overdue"


class PdcDialog(QDialog):
    def __init__(self, con, row=None, parent=None):
        super().__init__(parent)
        self.con = con
        self.setWindowTitle("Edit cheque" if row else "New cheque")
        self.setMinimumWidth(400)
        self.kind = QComboBox()
        for k, label in pdc.KINDS.items():
            self.kind.addItem(label, k)
        self.party = QComboBox()
        self.party.setEditable(False)
        self.no, self.bank = QLineEdit(), QLineEdit()
        self.date = QDateEdit(QDate.currentDate().addDays(7))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd MMM yyyy")
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 100_000_000)
        self.amount.setPrefix("₹ ")
        self.amount.setDecimals(2)
        self.remarks = QLineEdit()
        for w in (self.kind, self.party, self.no, self.bank, self.date, self.amount, self.remarks):
            w.setStyleSheet(INPUT)
        f = QFormLayout(self)
        for label, w in (("Type", self.kind), ("Party", self.party), ("Cheque no. *", self.no), ("Bank", self.bank),
                         ("Cheque date", self.date), ("Amount *", self.amount), ("Remarks", self.remarks)):
            f.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.kind.currentIndexChanged.connect(self.fill_parties)
        if row:
            self.kind.setCurrentIndex(self.kind.findData(row["kind"]))
        self.fill_parties()
        if row:
            self.party.setCurrentIndex(max(0, self.party.findData(row["party_id"])))
            self.no.setText(row["cheque_no"])
            self.bank.setText(row["bank"])
            self.date.setDate(QDate.fromString(row["cheque_date"], "yyyy-MM-dd"))
            self.amount.setValue(row["amount"])
            self.remarks.setText(row["remarks"] or "")
        self.pid = row["id"] if row else None

    def fill_parties(self, *_):
        self.party.clear()
        ptype = "customer" if self.kind.currentData() == "received" else "supplier"
        for p in db.list_parties(self.con, ptype):
            if p["name"] != "Walk-in Customer":
                self.party.addItem(p["name"], p["id"])

    def data(self):
        return {"kind": self.kind.currentData(), "party_id": self.party.currentData(), "cheque_no": self.no.text(),
                "bank": self.bank.text(), "cheque_date": self.date.date().toString("yyyy-MM-dd"),
                "amount": self.amount.value(), "remarks": self.remarks.text()}

    def accept(self):
        problem = pdc.validate(self.con, self.data(), self.pid)
        if problem:
            return QMessageBox.warning(self, "Check the details", problem)
        super().accept()


class PdcPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("PDC Register")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        bar = QHBoxLayout()
        self.kind, self.status = QComboBox(), QComboBox()
        for box, items in ((self.kind, [("All cheques", None), ("Received from customers", "received"),
                                         ("Issued to suppliers", "issued")]),
                           (self.status, [("Open (pending + deposited)", "open"), ("All statuses", None), ("Pending", "pending"),
                                          ("Deposited", "deposited"), ("Cleared", "cleared"), ("Bounced", "bounced")])):
            box.setStyleSheet(INPUT)
            for label, val in items:
                box.addItem(label, val)
            box.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search party, cheque no. or bank...")
        self.search.setStyleSheet(INPUT)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.kind)
        bar.addWidget(self.status)
        bar.addWidget(self.search, 1)
        new = QPushButton("+ New cheque")
        new.setStyleSheet(BTN)
        new.clicked.connect(self.new)
        bar.addWidget(new)
        bar.addWidget(export.ExcelButton(self.get_reps, "PDC_register"))
        v.addLayout(bar)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(TABLE)
        self.table.itemSelectionChanged.connect(self.buttons)
        self.table.doubleClicked.connect(self.edit)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.b = {}
        for key, label, fn, style in (("edit", "Edit", self.edit, BTN_GREY), ("deposited", "Mark deposited", lambda: self.move("deposited"), BTN),
                                      ("cleared", "Mark cleared", lambda: self.move("cleared"), BTN),
                                      ("bounced", "Mark bounced", lambda: self.move("bounced"), BTN_GREY),
                                      ("pending", "Present again", lambda: self.move("pending"), BTN_GREY),
                                      ("print", "Print cheque", self.print_cheque, BTN_GREY),
                                      ("delete", "Delete", self.delete, BTN_GREY)):
            b = QPushButton(label)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            self.b[key] = b
            row.addWidget(b)
        row.addStretch()
        self.total = QLabel()
        self.total.setStyleSheet("color:#64748b;")
        row.addWidget(self.total)
        v.addLayout(row)
        self.refresh()

    def refresh(self, *_):
        self.rows = pdc.listing(self.con, self.kind.currentData(), self.status.currentData(), self.search.text())
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            cells = [r["cheque_date"], r["party"], "Received" if r["kind"] == "received" else "Issued", r["cheque_no"], r["bank"],
                     f"{r['amount']:,.2f}", r["status"].title(), when_text(r)]
            cells[0] = QDate.fromString(r["cheque_date"], "yyyy-MM-dd").toString("dd MMM yyyy")
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 5:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if r["status"] == "bounced" or (r["status"] in ("pending", "deposited") and r["days_left"] < 0):
                    it.setForeground(QColor("#dc2626"))
                elif r["status"] == "pending" and r["days_left"] == 0:
                    it.setForeground(QColor("#d97706"))
                elif r["status"] == "cleared":
                    it.setForeground(QColor("#16a34a"))
                it.setData(Qt.UserRole, r["id"])
                self.table.setItem(i, c, it)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        rec = sum(r["amount"] for r in self.rows if r["kind"] == "received")
        iss = sum(r["amount"] for r in self.rows if r["kind"] == "issued")
        self.total.setText(f"{len(self.rows)} cheque(s)   |   Received ₹{rec:,.0f}   |   Issued ₹{iss:,.0f}")
        self.buttons()

    def current(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def buttons(self):
        r = self.current()
        st = r["status"] if r else None
        for key in ("deposited", "cleared", "bounced", "pending"):
            self.b[key].setEnabled(bool(r) and key in pdc.NEXT[st])
        self.b["edit"].setEnabled(st == "pending")
        self.b["delete"].setEnabled(st == "pending")
        self.b["print"].setEnabled(bool(r) and r["kind"] == "issued" and st in ("pending", "deposited"))

    def print_cheque(self):
        r = self.current()
        if r:
            ChequeDialog(self.con, r["party"], r["amount"], r["party_id"], r["payment_id"], r["cheque_no"], r["cheque_date"], self).exec()

    def new(self):
        d = PdcDialog(self.con, parent=self)
        if d.exec():
            try:
                pdc.save(self.con, d.data())
            except ValueError as e:
                QMessageBox.warning(self, "Cannot save", str(e))
            self.refresh()

    def edit(self, *_):
        r = self.current()
        if not r:
            return
        if r["status"] != "pending":
            return QMessageBox.information(self, "Cheque", "Only a pending cheque can be edited.")
        d = PdcDialog(self.con, r, self)
        if d.exec():
            try:
                pdc.save(self.con, d.data(), r["id"])
            except ValueError as e:
                QMessageBox.warning(self, "Cannot save", str(e))
            self.refresh()

    def move(self, new):
        r = self.current()
        if not r:
            return
        note = ""
        if new == "cleared":
            who = "from" if r["kind"] == "received" else "to"
            if QMessageBox.question(self, "Mark cleared", f"Record ₹{r['amount']:,.2f} {who} {r['party']}?\n\n"
                                    "This adds a payment and reduces their balance.") != QMessageBox.Yes:
                return
        elif new == "bounced":
            note, ok = QInputDialog.getText(self, "Cheque bounced", "Reason (optional, e.g. insufficient funds):")
            if not ok:
                return
        try:
            pdc.set_status(self.con, r["id"], new, note.strip())
        except ValueError as e:
            QMessageBox.warning(self, "Cannot change", str(e))
        self.refresh()

    def delete(self):
        r = self.current()
        if r and QMessageBox.question(self, "Delete cheque", f"Delete cheque {r['cheque_no']} of {r['party']}?") == QMessageBox.Yes:
            try:
                pdc.delete(self.con, r["id"])
            except ValueError as e:
                QMessageBox.warning(self, "Cannot delete", str(e))
            self.refresh()

    def get_reps(self):
        self.refresh()
        return [export.table_to_rep(self.table, "PDC Register")]

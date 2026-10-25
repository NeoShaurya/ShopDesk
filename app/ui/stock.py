"""Stock In / Out: receive purchases, adjust or write off stock, and see every movement."""
from datetime import date

from PySide6.QtCore import QDate, Qt
import sqlite3

from PySide6.QtWidgets import (QAbstractItemView, QGroupBox, QInputDialog, QListWidget, QListWidgetItem, QCheckBox, QComboBox, QCompleter, QDateEdit, QDoubleSpinBox,
                               QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import db
from ..theme import BTN, BTN_GREY, INPUT, LIST, TABLE
from .inventory import money
from .inventory_tools import LocationLevelsDialog
from .parties import PartyDialog

REASON = {"purchase": "Stock in", "sale": "Sale", "adjustment": "Adjustment", "expired": "Expired write-off",
          "return": "Return", "transfer": "Transfer"}
# label: (movement reason, +1 adds stock / -1 removes / 0 = set to counted quantity)
ADJUST = {"Damaged / lost / spoiled": ("adjustment", -1), "Expired - write off": ("expired", -1),
          "Returned to supplier": ("return", -1), "Customer return (add back)": ("return", 1),
          "Found extra stock (add)": ("adjustment", 1), "Set to counted quantity": ("adjustment", 0)}


def col(label, widget):
    box = QVBoxLayout()
    box.setSpacing(3)
    lab = QLabel(label)
    lab.setStyleSheet("color:#64748b;font-size:12px;")
    box.addWidget(lab)
    (box.addLayout if isinstance(widget, QLayout) else box.addWidget)(widget)
    return box


def make_table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().hide()
    t.setAlternatingRowColors(True)
    t.setStyleSheet(TABLE)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(0, QHeaderView.Stretch)
    return t


class AdjustTab(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        v = QVBoxLayout(self)
        v.setSpacing(14)
        self.expired = QLabel()
        self.expired.setWordWrap(True)
        self.wo = QPushButton("Write off ALL expired stock")
        self.wo.setStyleSheet(BTN)
        self.wo.clicked.connect(self.writeoff)
        row = QHBoxLayout()
        row.addWidget(self.expired, 1)
        row.addWidget(self.wo)
        v.addLayout(row)

        self.batch = QComboBox()
        self.batch.setStyleSheet(INPUT)
        self.kind = QComboBox()
        self.kind.setStyleSheet(INPUT)
        self.kind.addItems(list(ADJUST))
        self.qty = QSpinBox()
        self.qty.setRange(0, 10_000_000)
        self.qty.setStyleSheet(INPUT)
        self.note = QLineEdit()
        self.note.setStyleSheet(INPUT)
        self.note.setPlaceholderText("Optional note")
        apply_ = QPushButton("Apply")
        apply_.setStyleSheet(BTN)
        apply_.clicked.connect(self.apply)
        v.addLayout(col("Which batch?", self.batch))
        r2 = QHBoxLayout()
        r2.addLayout(col("What happened?", self.kind), 2)
        r2.addLayout(col("Quantity", self.qty))
        r2.addLayout(col("Note", self.note), 3)
        r2.addWidget(apply_, 0, Qt.AlignBottom)
        v.addLayout(r2)
        self.result = QLabel()
        self.result.setStyleSheet("color:#16a34a;")
        v.addWidget(self.result)
        v.addStretch()

    def refresh(self):
        keep = self.batch.currentData()
        self.batch.clear()
        for b in db.batches_for_adjust(self.con):
            exp = date.fromisoformat(b["expiry_date"]).strftime("%d %b %Y") if b["expiry_date"] else "no expiry"
            self.batch.addItem(f"{b['name']}  -  batch {b['batch_no']}  -  exp {exp}  -  {b['qty']} in stock", b["id"])
        if keep is not None:
            self.batch.setCurrentIndex(max(0, self.batch.findData(keep)))
        n, val = db.expired_stock(self.con)
        self.expired.setText(f"<b>{n} expired batch(es)</b> still on the shelf, worth ₹ {val:,.0f} at cost." if n
                             else "No expired stock on the shelf.")
        self.expired.setStyleSheet("color:#dc2626;font-size:14px;" if n else "color:#16a34a;font-size:14px;")
        self.wo.setEnabled(bool(n))

    def writeoff(self):
        n, val = db.expired_stock(self.con)
        if QMessageBox.question(self, "Write off expired stock", f"Remove {n} expired batch(es) worth ₹ {val:,.0f} "
                                "from stock?\nThis is logged in the history.") == QMessageBox.Yes:
            db.writeoff_expired(self.con)
            self.result.setText(f"Wrote off {n} expired batch(es).")
            self.refresh()

    def apply(self):
        bid = self.batch.currentData()
        if bid is None:
            return
        reason, sign = ADJUST[self.kind.currentText()]
        cur = db.get_batch(self.con, bid)["qty"]
        change = self.qty.value() - cur if sign == 0 else sign * self.qty.value()
        if change == 0:
            return QMessageBox.information(self, "No change", "Enter a quantity first." if sign else "Count matches stock.")
        try:
            db.adjust_stock(self.con, bid, change, reason, f"{self.kind.currentText()}. {self.note.text().strip()}".strip())
        except ValueError as e:
            return QMessageBox.warning(self, "Cannot adjust", str(e))
        self.result.setText(f"Done: stock changed by {change:+d}.")
        self.qty.setValue(0)
        self.note.clear()
        self.refresh()


class HistoryTab(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        v = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setStyleSheet(INPUT)
        self.search.setPlaceholderText("Search product or batch...")
        self.search.textChanged.connect(self.refresh)
        v.addWidget(self.search)
        self.table = make_table(["Product", "Batch", "When", "Change", "Type", "Note"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        note = QLabel("Showing the latest 300 movements.")
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)

    def refresh(self, *_):
        rows = db.list_movements(self.con, self.search.text().strip())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            when = date.fromisoformat(r["created_at"][:10]).strftime("%d %b") + r["created_at"][10:16]
            cells = [r["name"], r["batch_no"], when, f"{r['change_qty']:+d}", REASON.get(r["reason"], r["reason"]),
                     r["note"] or ""]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    it.setForeground(Qt.darkGreen if r["change_qty"] > 0 else Qt.red)
                self.table.setItem(i, c, it)


class GodownTab(QWidget):
    """Stock by location, transfers between locations, and managing the godowns themselves."""

    def __init__(self, con):
        super().__init__()
        self.con = con
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.locs = QListWidget()
        self.locs.setFixedWidth(230)
        self.locs.setStyleSheet(LIST)
        self.locs.currentRowChanged.connect(self.load_table)
        left.addWidget(self.locs, 1)
        for text, fn, style in [("+ New godown", self.new, BTN), ("Rename", self.rename, BTN_GREY),
                                ("Delete", self.delete, BTN_GREY)]:
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            left.addWidget(b)
        lv = QPushButton("Reorder levels...")
        lv.setStyleSheet(BTN_GREY)
        lv.clicked.connect(self.levels)
        left.addWidget(lv)
        note = QLabel("Bills are made from the main shop. Move stock from a godown to the main shop to sell it.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        left.addWidget(note)
        h.addLayout(left)

        right = QVBoxLayout()
        self.table = make_table(["Location", "Product", "Batch", "Expiry", "Qty"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        right.addWidget(self.table, 1)

        box = QGroupBox("Transfer stock")
        tv = QHBoxLayout(box)
        self.batch = QComboBox()
        self.batch.setStyleSheet(INPUT)
        self.batch.setMinimumWidth(260)
        self.src, self.dst = QComboBox(), QComboBox()
        for c in (self.src, self.dst):
            c.setStyleSheet(INPUT)
        self.qty = QSpinBox()
        self.qty.setRange(1, 10_000_000)
        self.qty.setStyleSheet(INPUT)
        self.note = QLineEdit()
        self.note.setStyleSheet(INPUT)
        self.note.setPlaceholderText("Optional note")
        go = QPushButton("Transfer")
        go.setStyleSheet(BTN)
        go.clicked.connect(self.transfer)
        tv.addLayout(col("Product / batch", self.batch), 3)
        tv.addLayout(col("From", self.src), 3)
        tv.addLayout(col("To", self.dst), 2)
        tv.addLayout(col("Qty", self.qty), 1)
        tv.addLayout(col("Note", self.note), 2)
        tv.addWidget(go, 0, Qt.AlignBottom)
        right.addWidget(box)
        h.addLayout(right, 1)
        self.batch.currentIndexChanged.connect(self.batch_changed)
        self.src.currentIndexChanged.connect(self.src_changed)

    def refresh(self):
        keep = self.current_gid()
        self.locs.blockSignals(True)
        self.locs.clear()
        all_ = QListWidgetItem("All locations")
        all_.setData(Qt.UserRole, None)
        self.locs.addItem(all_)
        for g in db.list_godowns(self.con):
            it = QListWidgetItem(g["name"] + ("   (sells here)" if g["is_main"] else ""))
            it.setData(Qt.UserRole, g["id"])
            self.locs.addItem(it)
        self.locs.blockSignals(False)
        idx = 0
        for i in range(self.locs.count()):
            if self.locs.item(i).data(Qt.UserRole) == keep:
                idx = i
        self.locs.setCurrentRow(idx)
        self.load_table()
        self.load_batches()

    def current_gid(self):
        it = self.locs.currentItem()
        return it.data(Qt.UserRole) if it else None

    def load_table(self, *_):
        rows = db.stock_by_location(self.con, self.current_gid())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            exp = date.fromisoformat(r["expiry_date"]).strftime("%d %b %Y") if r["expiry_date"] else "-"
            for c, val in enumerate([r["godown"], r["product"], r["batch_no"], exp, r["qty"]]):
                it = QTableWidgetItem(str(val))
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)

    def load_batches(self):
        keep = self.batch.currentData()
        seen = {}
        for r in db.stock_by_location(self.con):
            seen.setdefault(r["batch_id"], [r["product"], r["batch_no"], r["expiry_date"], 0])[3] += r["qty"]
        self.batch.blockSignals(True)
        self.batch.clear()
        for bid, (name, bno, exp, total) in seen.items():
            e = date.fromisoformat(exp).strftime("%d %b %Y") if exp else "no expiry"
            self.batch.addItem(f"{name}  -  batch {bno}  -  exp {e}  -  {total} in all", bid)
        self.batch.blockSignals(False)
        if keep is not None:
            self.batch.setCurrentIndex(max(0, self.batch.findData(keep)))
        self.batch_changed()

    def batch_changed(self, *_):
        bid = self.batch.currentData()
        self.src.blockSignals(True)
        self.src.clear()
        if bid is not None:
            for g in db.list_godowns(self.con):
                q = db.location_qty(self.con, bid, g["id"])
                if q > 0:
                    self.src.addItem(f"{g['name']}  ({q})", g["id"])
        self.src.blockSignals(False)
        self.src_changed()

    def src_changed(self, *_):
        bid, frm = self.batch.currentData(), self.src.currentData()
        self.dst.clear()
        for g in db.list_godowns(self.con):
            if g["id"] != frm:
                self.dst.addItem(g["name"], g["id"])
        self.qty.setMaximum(max(db.location_qty(self.con, bid, frm), 1) if (bid is not None and frm is not None) else 1)

    def transfer(self):
        bid, frm, to = self.batch.currentData(), self.src.currentData(), self.dst.currentData()
        if bid is None or frm is None or to is None:
            return QMessageBox.information(self, "Transfer", "Choose a batch and two different locations. "
                                           "(Add a godown first if you only have the main shop.)")
        try:
            db.transfer_stock(self.con, bid, frm, to, self.qty.value(), self.note.text().strip())
        except ValueError as e:
            return QMessageBox.warning(self, "Cannot transfer", str(e))
        self.note.clear()
        self.refresh()

    def new(self):
        name, ok = QInputDialog.getText(self, "New godown", "Name of the godown:")
        if ok and name.strip():
            try:
                db.save_godown(self.con, name.strip())
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "Name", "A location with that name already exists.")
            self.refresh()

    def levels(self):
        gid = self.current_gid()
        if gid is None:
            return QMessageBox.information(self, "Reorder levels", "Click one location first.")
        LocationLevelsDialog(self.con, gid, self).exec()
        self.refresh()

    def rename(self):
        gid = self.current_gid()
        if gid is None:
            return QMessageBox.information(self, "Rename", "Click one location first.")
        old = next(g["name"] for g in db.list_godowns(self.con) if g["id"] == gid)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old)
        if ok and name.strip():
            try:
                db.save_godown(self.con, name.strip(), gid)
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "Name", "A location with that name already exists.")
            self.refresh()

    def delete(self):
        gid = self.current_gid()
        if gid is None:
            return QMessageBox.information(self, "Delete", "Click one location first.")
        if QMessageBox.question(self, "Delete godown", "Delete this godown?") != QMessageBox.Yes:
            return
        try:
            db.delete_godown(self.con, gid)
        except ValueError as e:
            QMessageBox.information(self, "Can't delete", str(e))
        self.refresh()


class StockPage(QWidget):
    def __init__(self, con):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Stock In / Out")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab{padding:10px 18px;font-size:14px;}")
        self.parts = [AdjustTab(con), HistoryTab(con), GodownTab(con)]
        for w, name in zip(self.parts, ["Adjust / write off", "Movement history", "Godowns && transfers"]):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(lambda i: self.parts[i].refresh())
        v.addWidget(self.tabs, 1)
        self.refresh()

    def refresh(self):
        self.parts[self.tabs.currentIndex()].refresh()

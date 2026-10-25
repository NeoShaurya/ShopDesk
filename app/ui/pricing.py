"""Pricing & Offers: price lists (retail / wholesale / MRP / your own) and 'buy X get Y free' schemes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

import sqlite3

from .. import db
from ..theme import BTN, BTN_GREY, INPUT, LIST, TABLE

BASIS = (("Selling price", "sale_price"), ("MRP", "mrp"))


def new_table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.verticalHeader().hide()
    t.setAlternatingRowColors(True)
    t.setStyleSheet(TABLE)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    h.setSectionResizeMode(0, QHeaderView.Stretch)
    return t


class ListDialog(QDialog):
    def __init__(self, pl=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit price list" if pl else "New price list")
        self.setMinimumWidth(380)
        self.name = QLineEdit(pl["name"] if pl else "")
        self.basis = QComboBox()
        for label, key in BASIS:
            self.basis.addItem(label, key)
        self.pct = QDoubleSpinBox()
        self.pct.setRange(-90, 100)
        self.pct.setDecimals(1)
        self.pct.setSuffix(" %")
        if pl:
            self.basis.setCurrentIndex(self.basis.findData(pl["basis"]))
            self.pct.setValue(pl["adjust_percent"])
        f = QFormLayout(self)
        f.addRow("Name *", self.name)
        f.addRow("Start from", self.basis)
        f.addRow("Then add / subtract", self.pct)
        f.addRow(QLabel("Use a negative number for a lower price, e.g. -5 = 5% below.\n"
                        "You can also type a fixed price for individual products."))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def accept(self):
        if not self.name.text().strip():
            return QMessageBox.warning(self, "Name", "Please enter a name.")
        super().accept()


class PriceListTab(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self._loading = False
        self.rows = []
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.lists = QListWidget()
        self.lists.setFixedWidth(230)
        self.lists.setStyleSheet(LIST)
        self.lists.currentRowChanged.connect(self.load_table)
        left.addWidget(self.lists, 1)
        for text, fn, style in [("+ New list", self.new, BTN), ("Edit list", self.edit, BTN_GREY),
                                ("Delete list", self.delete, BTN_GREY)]:
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            left.addWidget(b)
        h.addLayout(left)
        right = QVBoxLayout()
        self.info = QLabel()
        self.info.setStyleSheet("font-size:14px;color:#334155;")
        right.addWidget(self.info)
        self.search = QLineEdit()
        self.search.setStyleSheet(INPUT)
        self.search.setPlaceholderText("Search product...")
        self.search.textChanged.connect(self.load_table)
        right.addWidget(self.search)
        self.table = new_table(["Product", "Normal price", "Fixed price (type to set)", "Price used"])
        self.table.itemChanged.connect(self.on_edit)
        right.addWidget(self.table, 1)
        note = QLabel("Customers get the price list assigned to them (Customers & Suppliers > Edit). "
                      "Customers with no list use the first one.")
        note.setStyleSheet("color:#64748b;")
        right.addWidget(note)
        h.addLayout(right, 1)

    def refresh(self):
        keep = self.current_id()
        self.lists.blockSignals(True)
        self.lists.clear()
        for i, pl in enumerate(db.list_price_lists(self.con)):
            it = QListWidgetItem(pl["name"] + ("   (default)" if i == 0 else ""))
            it.setData(Qt.UserRole, pl["id"])
            self.lists.addItem(it)
        self.lists.blockSignals(False)
        idx = 0
        for i in range(self.lists.count()):
            if self.lists.item(i).data(Qt.UserRole) == keep:
                idx = i
        self.lists.setCurrentRow(idx)
        self.load_table()

    def current_id(self):
        it = self.lists.currentItem()
        return it.data(Qt.UserRole) if it else None

    def load_table(self, *_):
        lid = self.current_id()
        if lid is None:
            return
        pl = db.get_price_list(self.con, lid)
        ov = db.list_overrides(self.con, lid)
        basis = dict((k, l) for l, k in BASIS)[pl["basis"]]
        adj = f"{pl['adjust_percent']:+g}%" if pl["adjust_percent"] else "no adjustment"
        self.info.setText(f"<b>{pl['name']}</b> - starts from {basis.lower()}, {adj}")
        q = self.search.text().strip().lower()
        self.rows = [p for p in db.products_with_base_prices(self.con) if q in p["name"].lower()]
        self._loading = True
        self.table.setRowCount(len(self.rows))
        for i, p in enumerate(self.rows):
            base = db.list_price(pl, {}, p["id"], p["sale_price"], p["mrp"]) if p["sale_price"] is not None else None
            used = db.list_price(pl, ov, p["id"], p["sale_price"], p["mrp"]) if p["sale_price"] is not None else None
            cells = [p["name"], "-" if base is None else f"{base:,.2f}", f"{ov[p['id']]:.2f}" if p["id"] in ov else "",
                     "-" if used is None else f"{used:,.2f}"]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsEditable if c == 2 else Qt.NoItemFlags))
                if c:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
        self._loading = False

    def on_edit(self, item):
        if self._loading or item.column() != 2:
            return
        text = item.text().strip().replace(",", "")
        try:
            price = None if not text else float(text)
            if price is not None and price < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Price", "Please enter a valid price (or leave blank to remove it).")
            return self.load_table()
        db.set_list_price(self.con, self.current_id(), self.rows[item.row()]["id"], price)
        self.load_table()

    def new(self):
        d = ListDialog(parent=self)
        if d.exec():
            try:
                db.save_price_list(self.con, d.name.text().strip(), d.basis.currentData(), d.pct.value())
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "Name", "A price list with that name already exists.")
            self.refresh()

    def edit(self):
        lid = self.current_id()
        if lid is None:
            return
        d = ListDialog(db.get_price_list(self.con, lid), self)
        if d.exec():
            try:
                db.save_price_list(self.con, d.name.text().strip(), d.basis.currentData(), d.pct.value(), lid)
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "Name", "A price list with that name already exists.")
            self.refresh()

    def delete(self):
        lid = self.current_id()
        if lid is None or QMessageBox.question(self, "Delete", "Delete this price list?\n"
                                               "Customers using it will go back to the default.") != QMessageBox.Yes:
            return
        try:
            db.delete_price_list(self.con, lid)
        except ValueError as e:
            QMessageBox.information(self, "Can't delete", str(e))
        self.refresh()


class SchemeDialog(QDialog):
    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New scheme")
        self.setMinimumWidth(360)
        self.product = QComboBox()
        for p in db.list_products(con):
            self.product.addItem(p["name"], p["id"])
        self.buy, self.free = QSpinBox(), QSpinBox()
        for s in (self.buy, self.free):
            s.setRange(1, 100000)
        self.buy.setValue(10)
        f = QFormLayout(self)
        f.addRow("Product", self.product)
        f.addRow("Customer buys", self.buy)
        f.addRow("Gets free", self.free)
        f.addRow(QLabel("e.g. buy 10, get 1 free: 25 bought earns 2 free."))
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class SchemeTab(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, fn, style in [("+ New scheme", self.new, BTN), ("Turn on / off", self.toggle, BTN_GREY),
                                ("Delete", self.delete, BTN_GREY)]:
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        v.addLayout(bar)
        self.table = new_table(["Product", "Offer", "Status"])
        v.addWidget(self.table, 1)
        note = QLabel("Free units are added automatically on the bill, are taken from stock, and print as a separate "
                      "'(free)' line. Discounts: type a Disc % on any bill line, or use the bill-level Discount box. "
                      "A customer's Trade discount applies to all their lines automatically.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)

    def refresh(self):
        self.rows = db.list_schemes(self.con)
        self.table.setRowCount(len(self.rows))
        for i, s in enumerate(self.rows):
            cells = [s["name"], f"Buy {s['buy_qty']}, get {s['free_qty']} free", "On" if s["active"] else "Off"]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if c == 2:
                    it.setForeground(Qt.darkGreen if s["active"] else Qt.gray)
                self.table.setItem(i, c, it)

    def selected(self):
        r = self.table.currentRow()
        if r < 0:
            QMessageBox.information(self, "Select a row", "Click a scheme first.")
            return None
        return self.rows[r]["id"]

    def new(self):
        if not db.list_products(self.con):
            return QMessageBox.information(self, "Scheme", "Add a product first.")
        d = SchemeDialog(self.con, self)
        if d.exec():
            db.save_scheme(self.con, d.product.currentData(), d.buy.value(), d.free.value())
            self.refresh()

    def toggle(self):
        sid = self.selected()
        if sid is not None:
            db.toggle_scheme(self.con, sid)
            self.refresh()

    def delete(self):
        sid = self.selected()
        if sid is not None and QMessageBox.question(self, "Delete", "Delete this scheme?") == QMessageBox.Yes:
            db.delete_scheme(self.con, sid)
            self.refresh()


class PricingPage(QWidget):
    def __init__(self, con):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Pricing & Offers")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabBar::tab{padding:10px 18px;font-size:14px;}")
        self.parts = [PriceListTab(con), SchemeTab(con)]
        for w, name in zip(self.parts, ["Price lists", "Schemes (buy X get Y free)"]):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(lambda i: self.parts[i].refresh())
        v.addWidget(self.tabs, 1)
        self.refresh()

    def refresh(self):
        self.parts[self.tabs.currentIndex()].refresh()

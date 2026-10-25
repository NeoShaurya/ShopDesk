"""Billing screen: pick customer, scan a barcode or type a product + Enter, adjust qty/price,
choose payment method / advance, save & print.
Stock comes from the nearest-expiry batch first, and expired batches can never be sold."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDoubleSpinBox, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import currency, drafts, db, extras
from ..theme import BTN, BTN_GREY, INPUT, TABLE
from .invoice_dialog import InvoiceDialog
from .parties import PartyDialog
from .payment import PaymentBox, rupees

COLS = ["Product", "Batch", "HSN", "Qty", "Free", "Price", "Disc %", "GST %", "Amount", ""]
C_QTY, C_FREE, C_PRICE, C_DISC = 3, 4, 5, 6


class Billing(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.cart = []            # [{pid, name, qty, price, gst, stock, batch, batch_qty, hsn}]
        self.by_label = {}
        self.by_id = {}
        self.inclusive = False
        self._loading = False
        self.plist, self.overrides, self.trade, self.schemes, self.gross = None, {}, 0.0, {}, 0.0

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(18)

        # ---------- left: customer, scan / add row, cart
        left = QVBoxLayout()
        left.setSpacing(10)
        title = QLabel("New bill")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        left.addWidget(title)

        crow = QHBoxLayout()
        crow.addWidget(QLabel("Customer"))
        self.customer = QComboBox()
        self.customer.setStyleSheet(INPUT)
        self.customer.setMinimumWidth(240)
        crow.addWidget(self.customer)
        newc = QPushButton("+ New customer")
        newc.setStyleSheet(BTN_GREY)
        newc.clicked.connect(self.new_customer)
        crow.addWidget(newc)
        self.terms = QLabel()
        self.terms.setStyleSheet("color:#64748b;")
        crow.addWidget(self.terms)
        self.series = QComboBox()
        self.series.setStyleSheet(INPUT)
        self.series.setToolTip("Bill number series")
        crow.addWidget(self.series)
        self.cur = QComboBox()
        self.cur.setStyleSheet(INPUT)
        self.cur.setToolTip("Currency of this bill")
        self.cur.currentIndexChanged.connect(self.update_totals)
        crow.addWidget(self.cur)
        crow.addStretch()
        last = QPushButton("Reprint last bill")
        last.setStyleSheet(BTN_GREY)
        last.clicked.connect(self.reprint_last)
        crow.addWidget(last)
        left.addLayout(crow)

        # barcode scanners type the code and press Enter, so a plain text box is all that is needed
        self.barcode = QLineEdit()
        self.barcode.setStyleSheet(INPUT)
        self.barcode.setPlaceholderText("Scan or type a barcode, then Enter")
        self.barcode.setProperty("enterAction", True)
        self.barcode.returnPressed.connect(self.add_barcode)
        left.addWidget(self.barcode)

        arow = QHBoxLayout()
        self.product = QComboBox()
        self.product.setEditable(True)
        self.product.setInsertPolicy(QComboBox.NoInsert)
        self.product.setStyleSheet(INPUT)
        self.product.lineEdit().setPlaceholderText("Or type a product name, press Enter to add")
        self.product.setProperty("enterAction", True)
        self.product.lineEdit().returnPressed.connect(self.add_item)
        self.qty = QSpinBox()
        self.qty.setRange(1, 1_000_000)
        self.qty.setPrefix("Qty  ")
        self.qty.setStyleSheet(INPUT)
        add = QPushButton("Add")
        add.setStyleSheet(BTN)
        add.clicked.connect(self.add_item)
        arow.addWidget(self.product, 1)
        arow.addWidget(self.qty)
        arow.addWidget(add)
        left.addLayout(arow)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setStyleSheet(TABLE)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemChanged.connect(self.on_item_changed)
        left.addWidget(self.table, 1)
        hint = QLabel("Tip: double-click a Qty or Price to change it.")
        hint.setStyleSheet("color:#64748b;")
        left.addWidget(hint)
        root.addLayout(left, 1)

        # ---------- right: totals + payment
        card = QFrame()
        card.setFixedWidth(340)
        card.setObjectName("tot")
        card.setStyleSheet("QFrame#tot{background:white;border:1px solid #e2e8f0;border-radius:10px;}")
        g = QGridLayout(card)
        g.setContentsMargins(18, 16, 18, 16)
        g.setVerticalSpacing(10)
        self.sub_name, self.sub_val = QLabel("Subtotal"), QLabel()
        self.tax_name, self.tax_val = QLabel("GST"), QLabel()
        self.discount = QDoubleSpinBox()
        self.discount.setRange(0, 10_000_000)
        self.discount.setPrefix("₹ ")
        self.discount.setStyleSheet(INPUT)
        self.discount.valueChanged.connect(self.update_totals)
        self.disc_mode = QComboBox()
        self.disc_mode.setStyleSheet(INPUT)
        self.disc_mode.addItem("₹", "amt")
        self.disc_mode.addItem("%", "pct")
        self.disc_mode.currentIndexChanged.connect(self._disc_mode_changed)
        drow = QHBoxLayout()
        drow.setContentsMargins(0, 0, 0, 0)
        drow.addWidget(self.discount, 1)
        drow.addWidget(self.disc_mode)
        self.total = QLabel()
        self.total.setStyleSheet("font-size:30px;font-weight:700;color:#2563eb;")
        self.fc = QLabel()
        self.fc.setStyleSheet("color:#64748b;")
        self.pay = PaymentBox()
        for r, (a, b) in enumerate([(self.sub_name, self.sub_val), (self.tax_name, self.tax_val)]):
            g.addWidget(a, r, 0)
            g.addWidget(b, r, 1, Qt.AlignRight)
        g.addWidget(QLabel("Bill discount"), 2, 0)
        g.addLayout(drow, 2, 1)
        g.addWidget(QLabel("TOTAL"), 3, 0)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        tbox.addWidget(self.total, 0, Qt.AlignRight)
        tbox.addWidget(self.fc, 0, Qt.AlignRight)
        g.addLayout(tbox, 3, 1)
        g.addWidget(self.pay, 4, 0, 1, 2)
        save = QPushButton("Save && Print")
        save.setStyleSheet(BTN)
        save.clicked.connect(lambda: self.save(True))
        save2 = QPushButton("Save only")
        save2.setStyleSheet(BTN_GREY)
        save2.clicked.connect(lambda: self.save(False))
        clear = QPushButton("Clear bill")
        clear.setStyleSheet(BTN_GREY)
        clear.clicked.connect(self.clear)
        g.addWidget(save, 5, 0, 1, 2)
        g.addWidget(save2, 6, 0, 1, 2)
        g.addWidget(clear, 7, 0, 1, 2)
        g.setRowStretch(8, 1)
        root.addWidget(card)

        self.customer.currentIndexChanged.connect(self.on_customer_changed)
        self.refresh()

    # ---------- data
    def refresh(self):
        """Reload products/customers (stock or settings may have changed elsewhere)."""
        self.inclusive = db.get_settings(self.con)["tax_inclusive"] == "1"
        keep = self.customer.currentData()
        self.customer.blockSignals(True)
        self.customer.clear()
        for c in db.list_customers(self.con):
            self.customer.addItem(c["name"], c["id"])
        if keep is not None:
            self.customer.setCurrentIndex(max(0, self.customer.findData(keep)))
        self.customer.blockSignals(False)
        self.schemes = db.schemes_map(self.con)
        keep_series = self.series.currentData()
        self.series.blockSignals(True)
        self.series.clear()
        for s in extras.series_list(self.con):
            self.series.addItem(f"{s['name']} ({s['prefix']})", s["id"])
        self.series.setCurrentIndex(max(0, self.series.findData(keep_series if keep_series is not None else extras.default_series_id(self.con))))
        self.series.blockSignals(False)
        self.series.setVisible(self.series.count() > 1)
        keep_cur = self.cur.currentData()
        self.cur.blockSignals(True)
        self.cur.clear()
        for r in currency.listing(self.con):
            self.cur.addItem(r["code"], r["code"])
        self.cur.setCurrentIndex(max(0, self.cur.findData(keep_cur or currency.base_code(self.con))))
        self.cur.blockSignals(False)
        self.cur.setVisible(self.cur.count() > 1)
        self.load_terms()
        rows = db.sellable_products(self.con)
        self.by_id = {r["id"]: r for r in rows}
        self.by_label = {f"{r['name']}   ({r['stock']} in stock)": r for r in rows}
        self.product.clear()
        self.product.addItems(list(self.by_label))
        self.product.setCurrentIndex(-1)
        comp = QCompleter(list(self.by_label), self.product)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.product.setCompleter(comp)
        # drop cart lines that can no longer be sold; cap quantities to current stock
        self.cart = [l for l in self.cart if l["pid"] in self.by_id]
        for l in self.cart:
            r = self.by_id[l["pid"]]
            l.update(stock=r["stock"], batch=r["batch_no"], batch_qty=r["batch_qty"], hsn=r["hsn_code"] or "")
            l["qty"] = self.max_qty(l["pid"], min(l["qty"], l["stock"]))
        self.cart = [l for l in self.cart if l["qty"] >= 1]
        self.reprice()

    def focus_first(self):
        self.barcode.setFocus()

    # ---------- customer terms: price list, trade discount, schemes
    def load_terms(self):
        cid = self.customer.currentData()
        self.plist, self.overrides = db.price_list_for_customer(self.con, cid)
        party = db.get_party(self.con, cid) if cid else None
        self.trade = ((party["trade_discount"] if party else 0) or 0)
        self.terms.setText(f"Price list: {self.plist['name']}" + (f"   |   Trade discount {self.trade:g}%" if self.trade else ""))

    def on_customer_changed(self, *_):
        self.load_terms()
        self.reprice()

    def price_for(self, row):
        return db.list_price(self.plist, self.overrides, row["id"], row["price"], row["mrp"])

    def reprice(self):
        """Apply the customer's price list and trade discount to every line you haven't typed over."""
        for l in self.cart:
            r = self.by_id.get(l["pid"])
            if r and not l["manual_price"]:
                l["price"] = self.price_for(r)
            if not l["manual_disc"]:
                l["disc"] = self.trade
        self.render()

    def free(self, l):
        return db.free_units(self.schemes.get(l["pid"]), l["qty"])

    def max_qty(self, pid, want):
        """Largest quantity <= want that still fits in stock once free scheme units are added."""
        stock = self.by_id[pid]["stock"] if pid in self.by_id else 0
        q = want
        while q > 0 and q + db.free_units(self.schemes.get(pid), q) > stock:
            q -= 1
        return q

    def _disc_mode_changed(self, *_):
        pct = self.disc_mode.currentData() == "pct"
        self.discount.blockSignals(True)
        self.discount.setValue(0)
        self.discount.setPrefix("" if pct else "₹ ")
        self.discount.setSuffix(" %" if pct else "")
        self.discount.blockSignals(False)
        self.update_totals()

    def discount_amount(self):
        v = self.discount.value()
        return round(self.gross * v / 100, 2) if self.disc_mode.currentData() == "pct" else v

    def new_customer(self):
        d = PartyDialog("customer", parent=self, con=self.con)
        if d.exec():
            cid = db.save_party(self.con, d.data(), None, "customer")
            self.refresh()
            self.customer.setCurrentIndex(self.customer.findData(cid))

    def resolve_product(self):
        text = self.product.currentText().strip()
        if not text:
            return None
        pid = db.product_id_by_barcode(self.con, text)      # a scanner may have typed into this box
        if pid in self.by_id:
            return self.by_id[pid]
        low = text.lower()
        for label, row in self.by_label.items():
            if label.lower() == low:
                return row
        hits = [row for label, row in self.by_label.items() if low in label.lower()]
        if len(hits) == 1:
            return hits[0]
        QMessageBox.information(self, "Product", "No matching product in stock." if not hits
                                else f"{len(hits)} products match - type a few more letters.")
        return None

    def _add(self, row, qty):
        line = next((l for l in self.cart if l["pid"] == row["id"]), None)
        new_qty = (line["qty"] if line else 0) + qty
        free = db.free_units(self.schemes.get(row["id"]), new_qty)
        if new_qty + free > row["stock"]:
            QMessageBox.warning(self, "Not enough stock", f"Only {row['stock']} of {row['name']} available"
                                + (f" (this order also gives {free} free)." if free else "."))
            return False
        if line:
            line["qty"] = new_qty
        else:
            self.cart.append({"pid": row["id"], "name": row["name"], "qty": qty, "price": self.price_for(row),
                              "disc": self.trade, "gst": row["gst_percent"], "stock": row["stock"],
                              "batch": row["batch_no"], "batch_qty": row["batch_qty"], "hsn": row["hsn_code"] or "",
                              "manual_price": False, "manual_disc": False})
        self.render()
        return True

    def add_item(self):
        row = self.resolve_product()
        if row is None:
            return
        if self._add(row, self.qty.value()):
            self.qty.setValue(1)
            self.product.setCurrentIndex(-1)
            self.product.clearEditText()
            self.product.setFocus()

    def add_barcode(self):
        code = self.barcode.text().strip()
        if not code:
            return
        self.barcode.clear()
        pid = db.product_id_by_barcode(self.con, code)
        if pid is None:
            return QMessageBox.warning(self, "Barcode not found", f"No product has the barcode {code}.\n"
                                       "Add it in Inventory (Edit product > Barcode).")
        if pid not in self.by_id:
            return QMessageBox.warning(self, "Not available", "That product is out of stock or fully expired.")
        self._add(self.by_id[pid], 1)
        self.barcode.setFocus()

    # ---------- table
    def render(self):
        self._loading = True
        for r in range(self.table.rowCount()):          # remove old row widgets so none are left drawn
            w = self.table.cellWidget(r, len(COLS) - 1)
            if w:
                w.hide()
                self.table.removeCellWidget(r, len(COLS) - 1)
        self.table.setRowCount(len(self.cart))
        for i, l in enumerate(self.cart):
            free = self.free(l)
            batch = l["batch"] + (" +" if l["qty"] + free > l["batch_qty"] else "")   # "+" = continues into the next batch
            amt = l["qty"] * l["price"] * (1 - l["disc"] / 100)
            cells = [l["name"], batch, l["hsn"], str(l["qty"]), f"+{free}" if free else "", f"{l['price']:.2f}",
                     f"{l['disc']:g}" if l["disc"] else "", f"{l['gst']:g}", f"{amt:,.2f}"]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                editable = c in (C_QTY, C_PRICE, C_DISC)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsEditable if editable else Qt.NoItemFlags))
                if c >= C_QTY:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == C_FREE and free:
                    it.setForeground(Qt.darkGreen)
                self.table.setItem(i, c, it)
            b = QPushButton("✕")
            b.setStyleSheet("QPushButton{border:none;color:#dc2626;font-size:15px;}QPushButton:hover{background:#fee2e2;}")
            b.clicked.connect(lambda _=False, pid=l["pid"]: self.remove(pid))
            self.table.setCellWidget(i, len(COLS) - 1, b)
        self._loading = False
        self.update_totals()

    def on_item_changed(self, item):
        if self._loading:
            return
        l = self.cart[item.row()]
        try:
            if item.column() == C_QTY:
                q = int(float(item.text()))
                if q < 1 or self.max_qty(l["pid"], q) < q:
                    QMessageBox.warning(self, "Quantity", f"Not enough stock. The most you can add is "
                                        f"{self.max_qty(l['pid'], l['stock'])} (free scheme units come out of stock too).")
                else:
                    l["qty"] = q
            elif item.column() == C_PRICE:
                p = float(item.text())
                if p < 0:
                    raise ValueError
                l["price"], l["manual_price"] = p, True
            elif item.column() == C_DISC:
                d = float(item.text() or 0)
                if not 0 <= d <= 100:
                    raise ValueError
                l["disc"], l["manual_disc"] = d, True
        except ValueError:
            QMessageBox.warning(self, "Invalid number", "Please enter a valid number.")
        self.render()

    def remove(self, pid):
        self.cart = [l for l in self.cart if l["pid"] != pid]
        self.render()

    # ---------- totals
    def lines(self):
        return [{"product_id": l["pid"], "qty": l["qty"], "free": self.free(l), "price": l["price"], "gst": l["gst"],
                 "disc": l["disc"]} for l in self.cart]

    def totals(self):
        return db.compute_totals(self.lines(), self.discount_amount(), self.inclusive)

    def update_totals(self, *_):
        t0 = db.compute_totals(self.lines(), 0, self.inclusive)
        self.gross = t0["subtotal"] + t0["tax"]
        pct = self.disc_mode.currentData() == "pct"
        self.discount.blockSignals(True)
        self.discount.setMaximum(100 if pct else max(self.gross, 0.01))
        self.discount.blockSignals(False)
        t = self.totals()
        if self.inclusive:
            self.sub_name.setText("Items total (incl. GST)")
            self.sub_val.setText(rupees(t["subtotal"] + t["tax"]))
            self.tax_name.setText("   of which GST")
        else:
            self.sub_name.setText("Subtotal")
            self.sub_val.setText(rupees(t["subtotal"]))
            self.tax_name.setText("GST")
        self.tax_val.setText(rupees(t["tax"]))
        self.total.setText(rupees(t["total"]))
        code = self.cur.currentData()
        if code and code != currency.base_code(self.con):
            _, rate = currency.resolve(self.con, code)
            self.fc.setText(f"{currency.show(code, currency.from_base(t['total'], rate))}  (1 {code} = {rate:g})")
        else:
            self.fc.setText("")
        self.pay.set_total(t["total"])

    # ---------- actions
    def draft_state(self):
        """What to auto-save, or None when there is nothing in the bill."""
        if not self.cart:
            return None
        return {"customer": self.customer.currentData(), "cart": self.cart, "discount": self.discount.value(),
                "disc_mode": self.disc_mode.currentData(), "pay": self.pay.state()}

    def draft_summary(self, d):
        return f"{len(d['cart'])} item(s)"

    def restore_draft(self, d):
        self.refresh()
        self.customer.setCurrentIndex(max(0, self.customer.findData(d.get("customer"))))
        self.disc_mode.blockSignals(True)
        self.disc_mode.setCurrentIndex(max(0, self.disc_mode.findData(d.get("disc_mode"))))
        self.disc_mode.blockSignals(False)
        self._disc_mode_changed()
        self.cart = d["cart"]
        self.refresh()                         # drops lines that can no longer be sold and re-checks stock
        self.discount.setValue(d.get("discount", 0))
        self.pay.restore(d.get("pay", {}))

    def autosave(self):
        d = self.draft_state()
        drafts.save(self.con, "bill", d) if d else drafts.clear(self.con, "bill")

    def reprint_last(self):
        sid = extras.last_bill(self.con)
        if not sid:
            return QMessageBox.information(self, "Reprint last bill", "No bill has been made yet.")
        InvoiceDialog(self.con, sid, self, autoprint=True).exec()

    def clear(self):
        drafts.clear(self.con, "bill")
        self.cart = []
        self.discount.setValue(0)
        self.pay.reset()
        self.render()

    def save(self, print_after=False):
        if not self.cart:
            return QMessageBox.information(self, "Empty bill", "Add at least one product first.")
        t = self.totals()
        method, paid, via = self.pay.values()
        due = self.pay.due_amount()
        if due > 0.005:
            if self.customer.currentText().startswith("Walk-in"):
                return QMessageBox.warning(self, "Payment", "Walk-in customers must pay in full.\n"
                                           "Choose or add a customer to keep a balance.")
            if QMessageBox.question(self, "Balance due", f"{rupees(due)} will be added to "
                                    f"{self.customer.currentText()}'s balance. Save bill?") != QMessageBox.Yes:
                return
        over = extras.credit_check(self.con, self.customer.currentData(), due)
        if over:
            if extras.credit_action(self.con) == "block":
                return QMessageBox.warning(self, "Credit limit", extras.credit_message(over) + "\n\nCollect more now, or raise the "
                                           "customer's credit limit in Customers & Suppliers.")
            if QMessageBox.question(self, "Credit limit", extras.credit_message(over) + "\n\nSave the bill anyway?") != QMessageBox.Yes:
                return
        low = db.below_cost(self.con, self.lines(), self.inclusive)
        if low and QMessageBox.question(
                self, "Selling below cost", "These items are priced below what they cost you:\n\n  " + "\n  ".join(low)
                + "\n\nSave the bill anyway?") != QMessageBox.Yes:
            return
        try:
            sale_id = db.create_sale(self.con, self.customer.currentData(), self.lines(), self.discount_amount(),
                                     paid, method, self.inclusive, via, self.series.currentData(), self.cur.currentData())
        except ValueError as e:
            QMessageBox.warning(self, "Cannot save", str(e))
            return self.refresh()
        self.clear()
        self.refresh()
        InvoiceDialog(self.con, sale_id, self, autoprint=print_after).exec()

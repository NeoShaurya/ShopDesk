"""Purchase entry: supplier invoice with full GST detail. Saving adds stock (batches),
purchase lines, stock movements and, if part is unpaid, the supplier's balance."""
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDateEdit, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
                               QTableWidgetItem)

from .. import currency, db, drafts
from .. import gst as gstmod
from ..gst import valid_hsn
from ..theme import BTN, BTN_GREY, INPUT
from .inventory import money
from .parties import PartyDialog
from .payment import PaymentBox, rupees
from .stock import col, make_table



class PurchasePage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.lines = []
        self.products = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        title = QLabel("Purchase entry")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        top = QHBoxLayout()
        self.supplier = QComboBox()
        self.supplier.setStyleSheet(INPUT)
        self.supplier.setMinimumWidth(240)
        newsup = QPushButton("+ New supplier")
        newsup.setStyleSheet(BTN_GREY)
        newsup.clicked.connect(self.new_supplier)
        self.inv_no = QLineEdit()
        self.inv_no.setStyleSheet(INPUT)
        self.inv_no.setPlaceholderText("Supplier's invoice number")
        self.inv_date = QDateEdit(QDate.currentDate())
        self.inv_date.setCalendarPopup(True)
        self.inv_date.setDisplayFormat("dd MMM yyyy")
        self.inv_date.setStyleSheet(INPUT)
        top.addLayout(col("Supplier", self.supplier))
        top.addWidget(newsup, 0, Qt.AlignBottom)
        top.addLayout(col("Supplier invoice no.", self.inv_no))
        top.addLayout(col("Invoice date", self.inv_date))
        self.cur = QComboBox()
        self.cur.setStyleSheet(INPUT)
        self.cur.currentIndexChanged.connect(self.cur_changed)
        self.cur_box = col("Supplier invoice currency", self.cur)
        top.addLayout(self.cur_box)
        self.godown = QComboBox()
        self.godown.setStyleSheet(INPUT)
        self.godown_box = col("Receive into", self.godown)
        top.addLayout(self.godown_box)
        top.addStretch()
        v.addLayout(top)

        # ---- item entry (two rows)
        self.product = QComboBox()
        self.product.setEditable(True)
        self.product.setInsertPolicy(QComboBox.NoInsert)
        self.product.setStyleSheet(INPUT)
        self.product.lineEdit().setPlaceholderText("Type product name...")
        self.product.activated.connect(lambda _i: self.prefill(self.product.currentText()))
        self.batch = QLineEdit()
        self.batch.setStyleSheet(INPUT)
        self.qty = QSpinBox()
        self.qty.setRange(1, 10_000_000)
        self.qty.setStyleSheet(INPUT)
        self.cost, self.mrp, self.price = money(QDoubleSpinBox()), money(QDoubleSpinBox()), money(QDoubleSpinBox())
        for s in (self.cost, self.mrp, self.price):
            s.setStyleSheet(INPUT)
        self.has_exp = QCheckBox()
        self.has_exp.setChecked(True)
        self.exp = QDateEdit(QDate.currentDate().addMonths(12))
        self.exp.setCalendarPopup(True)
        self.exp.setDisplayFormat("dd MMM yyyy")
        self.exp.setStyleSheet(INPUT)
        self.has_exp.toggled.connect(self.exp.setEnabled)
        expbox = QHBoxLayout()
        expbox.addWidget(self.has_exp)
        expbox.addWidget(self.exp)
        self.gst = QComboBox()
        self.gst.setStyleSheet(INPUT)
        rates = gstmod.rates(con)
        for r in rates:
            self.gst.addItem(f"{r:g}%", r)
        self.gst.setCurrentIndex(rates.index(12.0) if 12.0 in rates else min(1, len(rates) - 1))
        self.hsn = QLineEdit()
        self.hsn.setStyleSheet(INPUT)
        self.hsn.setPlaceholderText("4, 6 or 8 digits")
        add = QPushButton("Add item")
        add.setStyleSheet(BTN)
        add.clicked.connect(self.add_line)
        r1 = QHBoxLayout()
        r1.addLayout(col("Product", self.product), 4)
        r1.addLayout(col("Batch no.", self.batch), 1)
        r1.addLayout(col("Qty", self.qty), 1)
        r1.addLayout(col("Cost price (excl. GST)", self.cost), 1)
        r1.addLayout(col("MRP", self.mrp), 1)
        r1.addLayout(col("Selling price", self.price), 1)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addLayout(col("Expiry (tick = has expiry)", expbox))
        r2.addLayout(col("GST rate", self.gst))
        r2.addLayout(col("HSN code", self.hsn))
        r2.addStretch()
        r2.addWidget(add, 0, Qt.AlignBottom)
        v.addLayout(r2)

        self.table = make_table(["Product", "Batch", "Expiry", "Qty", "Cost", "MRP", "GST", "HSN", "Taxable",
                                 "GST amt", "Total", ""])
        v.addWidget(self.table, 1)

        # ---- bottom: totals + payment
        bottom = QHBoxLayout()
        tot = QFrame()
        tot.setObjectName("card")
        tot.setStyleSheet("QFrame#card{background:white;border:1px solid #e2e8f0;border-radius:10px;}")
        tv = QVBoxLayout(tot)
        self.l_taxable, self.l_gst, self.l_total = QLabel(), QLabel(), QLabel()
        self.l_total.setStyleSheet("font-size:26px;font-weight:700;color:#2563eb;")
        for lab in (self.l_taxable, self.l_gst, self.l_total):
            tv.addWidget(lab)
        bottom.addWidget(tot, 1)
        pay = QFrame()
        pay.setObjectName("card")
        pay.setStyleSheet("QFrame#card{background:white;border:1px solid #e2e8f0;border-radius:10px;}")
        pay.setFixedWidth(360)
        pv = QVBoxLayout(pay)
        self.pay = PaymentBox()
        pv.addWidget(self.pay)
        save = QPushButton("Save purchase")
        save.setStyleSheet(BTN)
        save.clicked.connect(self.save)
        pv.addWidget(save)
        bottom.addWidget(pay)
        v.addLayout(bottom)
        self.render()

    # ---- data
    def cur_rate(self):
        return currency.resolve(self.con, self.cur.currentData())[1] if self.cur.count() else 1.0

    def cur_changed(self, *_):
        """Cost prices are typed in the invoice's currency and saved in the base currency."""
        code = self.cur.currentData() or currency.base_code(self.con)
        self.cost.setPrefix("₹ " if code == "INR" else code + " ")

    def refresh(self):
        keep_cur = self.cur.currentData()
        self.cur.blockSignals(True)
        self.cur.clear()
        for r in currency.listing(self.con):
            self.cur.addItem(r["code"], r["code"])
        self.cur.setCurrentIndex(max(0, self.cur.findData(keep_cur or currency.base_code(self.con))))
        self.cur.blockSignals(False)
        for i in range(self.cur_box.count()):
            w = self.cur_box.itemAt(i).widget()
            if w:
                w.setVisible(self.cur.count() > 1)
        self.cur_changed()
        keep = self.supplier.currentData()
        self.supplier.clear()
        self.supplier.addItem("- none / cash purchase -", None)
        for s in db.list_parties(self.con, "supplier"):
            self.supplier.addItem(s["name"], s["id"])
        if keep is not None:
            self.supplier.setCurrentIndex(max(0, self.supplier.findData(keep)))
        keep_g = self.godown.currentData()
        self.godown.clear()
        gods = db.list_godowns(self.con)
        for g in gods:
            self.godown.addItem(g["name"], g["id"])
        if keep_g is not None:
            self.godown.setCurrentIndex(max(0, self.godown.findData(keep_g)))
        for i in range(self.godown_box.count()):
            w = self.godown_box.itemAt(i).widget()
            if w:
                w.setVisible(len(gods) > 1)
        self.products = {p["name"]: p for p in db.list_products(self.con)}
        self.product.clear()
        self.product.addItems(list(self.products))
        self.product.setCurrentIndex(-1)
        comp = QCompleter(list(self.products), self.product)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.activated.connect(self.prefill)
        self.product.setCompleter(comp)

    def focus_first(self):
        self.inv_no.setFocus()

    def new_supplier(self):
        d = PartyDialog("supplier", parent=self)
        if d.exec():
            sid = db.save_party(self.con, d.data(), None, "supplier")
            self.refresh()
            self.supplier.setCurrentIndex(self.supplier.findData(sid))

    def prefill(self, name):
        p = self.products.get(name)
        if not p:
            return
        last = db.latest_batch_prices(self.con, p["id"])
        if last:
            self.cost.setValue(currency.from_base(last["cost_price"], self.cur_rate()))
            self.mrp.setValue(last["mrp"])
            self.price.setValue(last["sale_price"])
        idx = self.gst.findData(float(p["gst_percent"] or 0))
        if idx >= 0:
            self.gst.setCurrentIndex(idx)
        self.hsn.setText(p["hsn_code"] or "")

    def resolve(self):
        t = self.product.currentText().strip().lower()
        hits = [n for n in self.products if n.lower() == t] or [n for n in self.products if t and t in n.lower()]
        if len(hits) == 1:
            return hits[0]
        QMessageBox.information(self, "Product", "Product not found. Add it in Inventory first (+ New product)."
                                if not hits else f"{len(hits)} products match - type a few more letters.")
        return None

    def add_line(self):
        name = self.resolve()
        if not name:
            return
        if not self.batch.text().strip():
            return QMessageBox.warning(self, "Batch", "Enter the batch number printed on the pack.")
        hsn = self.hsn.text().strip()
        if hsn and not valid_hsn(hsn):
            return QMessageBox.warning(self, "HSN code", "HSN must be 4, 6 or 8 digits (or leave it blank).")
        if self.cost.value() <= 0:
            return QMessageBox.warning(self, "Cost price", "Enter the cost price (without GST).")
        self.lines.append({"product_id": self.products[name]["id"], "name": name, "batch_no": self.batch.text().strip(),
                           "expiry_date": self.exp.date().toString("yyyy-MM-dd") if self.has_exp.isChecked() else None,
                           "qty": self.qty.value(), "cost_price": round(self.cost.value() * self.cur_rate(), 4), "mrp": self.mrp.value(),
                           "sale_price": self.price.value(), "gst": self.gst.currentData(), "hsn": hsn})
        self.batch.clear()
        self.qty.setValue(1)
        self.product.setCurrentIndex(-1)
        self.product.clearEditText()
        self.product.setFocus()
        self.render()

    def remove(self, n):
        del self.lines[n]
        self.render()

    def render(self):
        for r in range(self.table.rowCount()):          # remove old row widgets so none are left drawn
            w = self.table.cellWidget(r, 11)
            if w:
                w.hide()
                self.table.removeCellWidget(r, 11)
        self.table.setRowCount(len(self.lines))
        sums = [0.0, 0.0, 0.0]
        for i, l in enumerate(self.lines):
            tv, ga, lt = db.purchase_amounts(l)
            for k, x in enumerate((tv, ga, lt)):
                sums[k] += x
            exp = date.fromisoformat(l["expiry_date"]).strftime("%d %b %Y") if l["expiry_date"] else "-"
            cells = [l["name"], l["batch_no"], exp, l["qty"], f"{l['cost_price']:,.2f}", f"{l['mrp']:,.2f}",
                     f"{l['gst']}%", l["hsn"] or "", f"{tv:,.2f}", f"{ga:,.2f}", f"{lt:,.2f}"]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(str(val))
                if c >= 3 and c != 7:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
            b = QPushButton("✕")
            b.setStyleSheet("QPushButton{border:none;color:#dc2626;font-size:15px;}QPushButton:hover{background:#fee2e2;}")
            b.clicked.connect(lambda _=False, n=i: self.remove(n))
            self.table.setCellWidget(i, 11, b)
        self.l_taxable.setText(f"Taxable value    {rupees(sums[0])}")
        self.l_gst.setText(f"GST    {rupees(sums[1])}")
        self.l_total.setText(f"Total    {rupees(sums[2])}")
        self.pay.set_total(sums[2])

    def draft_state(self):
        if not self.lines:
            return None
        return {"supplier": self.supplier.currentData(), "inv_no": self.inv_no.text(),
                "inv_date": self.inv_date.date().toString("yyyy-MM-dd"), "godown": self.godown.currentData(),
                "lines": self.lines, "pay": self.pay.state()}

    def draft_summary(self, d):
        return f"{len(d['lines'])} item(s)"

    def restore_draft(self, d):
        self.refresh()
        self.supplier.setCurrentIndex(max(0, self.supplier.findData(d.get("supplier"))))
        self.godown.setCurrentIndex(max(0, self.godown.findData(d.get("godown"))))
        self.inv_no.setText(d.get("inv_no", ""))
        self.inv_date.setDate(QDate.fromString(d.get("inv_date", ""), "yyyy-MM-dd"))
        self.lines = d["lines"]
        self.render()
        self.pay.restore(d.get("pay", {}))

    def autosave(self):
        d = self.draft_state()
        drafts.save(self.con, "purchase", d) if d else drafts.clear(self.con, "purchase")

    def save(self):
        if not self.lines:
            return QMessageBox.information(self, "Nothing to save", "Add at least one item first.")
        method, paid, via = self.pay.values()
        sup = self.supplier.currentData()
        if self.pay.due_amount() > 0.005 and sup is None:
            return QMessageBox.warning(self, "Supplier needed", "To leave a balance unpaid, choose or add the supplier.")
        try:
            db.create_purchase(self.con, sup, self.inv_no.text().strip(), self.inv_date.date().toString("yyyy-MM-dd"),
                               self.lines, method, paid, via, self.godown.currentData(), self.cur.currentData())
        except ValueError as e:
            return QMessageBox.warning(self, "Cannot save", str(e))
        except Exception as e:
            return QMessageBox.warning(self, "Could not save", str(e))
        n = len(self.lines)
        drafts.clear(self.con, "purchase")
        self.lines = []
        self.inv_no.clear()
        self.pay.reset()
        self.render()
        QMessageBox.information(self, "Purchase saved", f"{n} item(s) added to stock.")

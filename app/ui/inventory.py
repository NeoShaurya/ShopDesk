"""Inventory screen: one table row per batch, with search, filters and add/edit dialogs."""
import sqlite3
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit,
                               QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import db, export
from .. import gst as gstmod
from .inventory_tools import LabelsDialog, PriceHistoryDialog

HEADERS = ["Product", "Category", "Batch", "Expiry", "Qty", "Cost", "MRP", "Price", "Export", "Status", "In godowns"]
BTN = ("QPushButton{background:#2563eb;color:white;border:none;border-radius:6px;padding:8px 14px;font-weight:600;}"
       "QPushButton:hover{background:#1d4ed8;}")
BTN_GREY = ("QPushButton{background:white;color:#0f172a;border:1px solid #cbd5e1;border-radius:6px;padding:8px 14px;}"
            "QPushButton:hover{background:#f1f5f9;}")


def money(sb: QDoubleSpinBox):
    sb.setRange(0, 10_000_000)
    sb.setDecimals(2)
    sb.setPrefix("₹ ")
    return sb


class ProductDialog(QDialog):
    def __init__(self, con, pid=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit product" if pid else "New product")
        self.setMinimumWidth(360)
        p = db.get_product(con, pid) if pid else None
        self.name = QLineEdit(p["name"] if p else "")
        self.sku = QLineEdit(p["sku"] or "" if p else "")
        self.category = QLineEdit(p["category"] or "" if p else "")
        self.hsn = QLineEdit(p["hsn_code"] or "" if p else "")
        self.barcode = QLineEdit(p["barcode"] or "" if p else "")
        self.barcode.setPlaceholderText("Scan with the barcode reader, or type (optional)")
        self.unit = QLineEdit(p["unit"] if p else "pcs")
        self.gst = QComboBox()
        rates = gstmod.rates(con)
        self.allowed = list(rates)
        cur = float(p["gst_percent"]) if p else 12.0
        if cur not in rates:                                  # an older, non-standard rate: show it so it can be fixed
            rates.append(cur)
        for r in rates:
            self.gst.addItem(f"{r:g} %", r)
        self.gst.setCurrentIndex(rates.index(cur))
        self.reorder = QSpinBox(); self.reorder.setRange(0, 1_000_000)
        self.reorder.setValue(p["reorder_level"] if p else 10)
        f = QFormLayout(self)
        for label, w in [("Name *", self.name), ("SKU / code", self.sku), ("Category", self.category), ("HSN code", self.hsn), ("Barcode", self.barcode),
                         ("Unit", self.unit), ("GST", self.gst), ("Reorder when stock ≤", self.reorder)]:
            f.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def accept(self):
        problem = gstmod.check_product(self.data(), self.allowed)
        if problem:
            return QMessageBox.warning(self, "Check the details", problem)
        super().accept()

    def data(self):
        return {"name": self.name.text().strip(), "sku": self.sku.text().strip() or None,
                "category": self.category.text().strip(), "hsn_code": self.hsn.text().strip() or None, "barcode": self.barcode.text().strip() or None, "unit": self.unit.text().strip() or "pcs",
                "gst_percent": self.gst.currentData(), "reorder_level": self.reorder.value()}


class BatchDialog(QDialog):
    def __init__(self, con, product_id=None, batch_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit batch" if batch_id else "Add batch / stock")
        self.setMinimumWidth(380)
        b = db.get_batch(con, batch_id) if batch_id else None
        self.product = QComboBox()
        for p in db.list_products(con):
            self.product.addItem(p["name"], p["id"])
        pid = b["product_id"] if b else product_id
        if pid is not None:
            self.product.setCurrentIndex(max(0, self.product.findData(pid)))
        self.product.setEnabled(not batch_id)
        self.batch_no = QLineEdit(b["batch_no"] if b else "")
        self.has_expiry = QCheckBox("Has expiry date")
        self.expiry = QDateEdit(); self.expiry.setCalendarPopup(True); self.expiry.setDisplayFormat("dd MMM yyyy")
        has = bool(b and b["expiry_date"]) or not b
        self.has_expiry.setChecked(has)
        self.expiry.setDate(QDate.fromString(b["expiry_date"], "yyyy-MM-dd") if b and b["expiry_date"]
                            else QDate.currentDate().addMonths(6))
        self.has_expiry.toggled.connect(self.expiry.setEnabled)
        self.expiry.setEnabled(has)
        self.qty = QSpinBox(); self.qty.setRange(0, 10_000_000); self.qty.setValue(b["qty"] if b else 0)
        self.cost = money(QDoubleSpinBox()); self.mrp = money(QDoubleSpinBox()); self.price = money(QDoubleSpinBox())
        if b:
            self.cost.setValue(b["cost_price"]); self.mrp.setValue(b["mrp"]); self.price.setValue(b["sale_price"])
        self.export = QCheckBox("Mark ready for export / sale today")
        self.export.setChecked(bool(b and b["export_ready"]))
        f = QFormLayout(self)
        for label, w in [("Product", self.product), ("Batch no. *", self.batch_no), ("", self.has_expiry),
                         ("Expiry", self.expiry), ("Quantity", self.qty), ("Cost price", self.cost),
                         ("MRP", self.mrp), ("Selling price", self.price), ("", self.export)]:
            f.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def accept(self):
        if not self.batch_no.text().strip():
            return QMessageBox.warning(self, "Missing batch", "Please enter a batch number.")
        if self.product.currentData() is None:
            return QMessageBox.warning(self, "No product", "Create a product first.")
        super().accept()

    def data(self):
        return {"product_id": self.product.currentData(), "batch_no": self.batch_no.text().strip(),
                "expiry_date": self.expiry.date().toString("yyyy-MM-dd") if self.has_expiry.isChecked() else None,
                "qty": self.qty.value(), "cost_price": self.cost.value(), "mrp": self.mrp.value(),
                "sale_price": self.price.value(), "export_ready": 1 if self.export.isChecked() else 0}


class Inventory(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Inventory"); title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        bar = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search product, code or batch...")
        self.search.setStyleSheet("padding:8px;border:1px solid #cbd5e1;border-radius:6px;background:white;")
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        for label, key in [("All items", "all"), ("Low stock", "low"), ("Expiring ≤ 30 days", "expiring"),
                           ("Ready for export", "export")]:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.search, 1); bar.addWidget(self.filter)
        for text, fn, style in [("+ New product", self.new_product, BTN), ("+ Add batch", self.new_batch, BTN),
                                ("Edit", self.edit_selected, BTN_GREY), ("Toggle export", self.toggle_export, BTN_GREY), ("Price history", self.price_history, BTN_GREY),
                                ("Print labels", self.labels, BTN_GREY), ("Export Excel", self.export_excel, BTN_GREY),
                                ("Delete", self.delete_selected, BTN_GREY)]:
            b = QPushButton(text); b.setStyleSheet(style); b.clicked.connect(fn); bar.addWidget(b)
        v.addLayout(bar)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("QTableWidget{background:white;alternate-background-color:#f8fafc;"
                                 "border:1px solid #e2e8f0;gridline-color:#f1f5f9;}"
                                 "QHeaderView::section{background:#f1f5f9;padding:8px;border:none;font-weight:600;color:#475569;}"
                                 "QTableWidget::item:selected{background:#dbeafe;color:#0f172a;}")
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.edit_selected)
        v.addWidget(self.table, 1)
        self.count = QLabel(); self.count.setStyleSheet("color:#64748b;")
        v.addWidget(self.count)
        self.refresh()

    # ----- table -----
    def refresh(self, *_):
        rows = db.list_inventory(self.con, self.search.text().strip(), self.filter.currentData())
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            status, color = self.status(r)
            exp = date.fromisoformat(r["expiry_date"]).strftime("%d %b %Y") if r["expiry_date"] else "-"
            vals = [r["name"], r["category"] or "", r["batch_no"] or "(no batch)", exp,
                    "" if r["qty"] is None else r["qty"],
                    "" if r["cost_price"] is None else f"{r['cost_price']:,.2f}",
                    "" if r["mrp"] is None else f"{r['mrp']:,.2f}",
                    "" if r["sale_price"] is None else f"{r['sale_price']:,.2f}",
                    "Yes" if r["export_ready"] else "", status,
                    "" if not r["in_godowns"] else r["in_godowns"]]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if c in (4, 5, 6, 7, 10):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 9:
                    it.setForeground(QColor(color))
                    f = it.font(); f.setBold(True); it.setFont(f)
                if c == 0:
                    it.setData(Qt.UserRole, (r["product_id"], r["batch_id"]))
                self.table.setItem(i, c, it)
        self.count.setText(f"{len(rows)} rows")

    @staticmethod
    def status(r):
        if r["batch_id"] is None:
            return "No stock", "#64748b"
        if r["qty"] > 0 and r["days_left"] is not None:
            if r["days_left"] < 0:
                return "Expired", "#dc2626"
            if r["days_left"] <= 7:
                return f"Expires in {r['days_left']}d", "#dc2626"
            if r["days_left"] <= 30:
                return f"Expires in {r['days_left']}d", "#d97706"
        if r["total_stock"] <= r["reorder_level"]:
            return "Low stock", "#d97706"
        return "OK", "#16a34a"

    def selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select a row", "Click a row in the table first.")
            return None
        return self.table.item(row, 0).data(Qt.UserRole)

    # ----- actions -----
    def price_history(self):
        sel = self.selected()
        if sel:
            PriceHistoryDialog(self.con, sel[0], self).exec()

    def labels(self):
        row = self.table.currentRow()
        sel = self.table.item(row, 0).data(Qt.UserRole) if row >= 0 else None
        LabelsDialog(self.con, sel[0] if sel else None, self).exec()

    def new_product(self):
        d = ProductDialog(self.con, parent=self)
        if d.exec():
            try:
                pid = db.save_product(self.con, d.data())
            except sqlite3.IntegrityError:
                return QMessageBox.warning(self, "Duplicate", "Another product already uses that SKU or barcode.")
            except ValueError as e:
                return QMessageBox.warning(self, "Check the details", str(e))
            self.refresh()
            if QMessageBox.question(self, "Add stock?", "Product saved. Add its first batch now?") == QMessageBox.Yes:
                self.new_batch(pid)

    def new_batch(self, product_id=None):
        pid = product_id if isinstance(product_id, int) else None
        if pid is None and self.table.currentRow() >= 0:
            pid = self.table.item(self.table.currentRow(), 0).data(Qt.UserRole)[0]
        d = BatchDialog(self.con, product_id=pid, parent=self)
        if d.exec():
            self.save_batch(d.data())

    def save_batch(self, data, bid=None):
        try:
            db.save_batch(self.con, data, bid)
        except sqlite3.IntegrityError:
            return QMessageBox.warning(self, "Duplicate batch", "This product already has a batch with that number.")
        self.refresh()

    def edit_selected(self, *_):
        sel = self.selected()
        if not sel:
            return
        pid, bid = sel
        if bid is None:
            d = ProductDialog(self.con, pid, self)
            if d.exec():
                self.save_product(d.data(), pid)
            return
        box = QMessageBox(self); box.setWindowTitle("Edit")
        box.setText("What do you want to edit?")
        b_batch = box.addButton("This batch", QMessageBox.AcceptRole)
        b_prod = box.addButton("The product", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() == b_batch:
            d = BatchDialog(self.con, batch_id=bid, parent=self)
            if d.exec():
                self.save_batch(d.data(), bid)
        elif box.clickedButton() == b_prod:
            d = ProductDialog(self.con, pid, self)
            if d.exec():
                self.save_product(d.data(), pid)

    def save_product(self, data, pid):
        try:
            db.save_product(self.con, data, pid)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Duplicate", "Another product already uses that SKU or barcode.")
        except ValueError as e:
            QMessageBox.warning(self, "Check the details", str(e))
        self.refresh()

    def export_excel(self):
        export.save_dialog(self, [export.table_to_rep(self.table, "Inventory")], "Inventory")

    def toggle_export(self):
        sel = self.selected()
        if not sel or sel[1] is None:
            return
        b = db.get_batch(self.con, sel[1])
        db.set_export_ready(self.con, sel[1], not b["export_ready"])
        self.refresh()

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            return
        pid, bid = sel
        if bid is None:
            if QMessageBox.question(self, "Remove product", "Remove this product from the list?") == QMessageBox.Yes:
                db.archive_product(self.con, pid); self.refresh()
            return
        if QMessageBox.question(self, "Delete batch", "Delete this batch permanently?") == QMessageBox.Yes:
            try:
                db.delete_batch(self.con, bid)
            except sqlite3.IntegrityError:
                self.con.rollback()
                QMessageBox.information(self, "Can't delete",
                                        "This batch has sales or purchase history. Edit it and set the quantity to 0 instead.")
            self.refresh()

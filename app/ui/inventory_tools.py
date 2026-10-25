"""Small Inventory / Stock dialogs: price history, reorder levels per location, barcode labels."""
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPdfWriter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import db, extras, labels
from ..theme import BTN, BTN_GREY, INPUT, TABLE


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setAlternatingRowColors(True)
    t.setStyleSheet(TABLE)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


class PriceHistoryDialog(QDialog):
    def __init__(self, con, product_id, parent=None):
        super().__init__(parent)
        name = db.get_product(con, product_id)["name"]
        self.setWindowTitle(f"Price history - {name}")
        self.resize(640, 420)
        v = QVBoxLayout(self)
        head = QLabel(f"Every price this product has had, newest first: {name}")
        head.setStyleSheet("font-size:15px;font-weight:700;")
        v.addWidget(head)
        rows = extras.price_history(con, product_id)
        t = _table(["Date", "Batch", "Cost", "MRP", "Selling price", "What happened"])
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["changed_at"][:16], r["batch_no"] or "", f"{r['cost_price']:,.2f}", f"{r['mrp']:,.2f}", f"{r['sale_price']:,.2f}",
                    "Batch added" if r["why"] == "new batch" else "Price changed"]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c in (2, 3, 4):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(i, c, it)
        v.addWidget(t, 1)
        close = QPushButton("Close")
        close.setStyleSheet(BTN)
        close.clicked.connect(self.accept)
        v.addWidget(close, 0, Qt.AlignRight)


class LocationLevelsDialog(QDialog):
    """Reorder level of each product at one location. Empty = use the product's own level."""

    def __init__(self, con, godown_id, parent=None):
        super().__init__(parent)
        self.con, self.gid = con, godown_id
        name = next(g["name"] for g in db.list_godowns(con) if g["id"] == godown_id)
        self.setWindowTitle(f"Reorder levels - {name}")
        self.resize(560, 520)
        v = QVBoxLayout(self)
        note = QLabel(f"Warn when a product at {name} falls to this level or below. Leave a box on \"Product's level\" to use the "
                      "level set on the product.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search product...")
        self.search.setStyleSheet(INPUT)
        self.search.textChanged.connect(self.filter)
        v.addWidget(self.search)
        self.prods = con.execute("SELECT id, name, reorder_level FROM products WHERE active = 1 ORDER BY name").fetchall()
        self.levels = extras.location_levels(con, godown_id)
        self.table = _table(["Product", "Product's level", "Level here"])
        self.table.setRowCount(len(self.prods))
        self.spins = {}
        for i, p in enumerate(self.prods):
            self.table.setItem(i, 0, QTableWidgetItem(p["name"]))
            it = QTableWidgetItem(str(p["reorder_level"]))
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 1, it)
            sp = QSpinBox()
            sp.setRange(-1, 1_000_000)
            sp.setSpecialValueText("Product's level")
            sp.setValue(self.levels.get(p["id"], -1))
            self.table.setCellWidget(i, 2, sp)
            self.spins[p["id"]] = sp
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        save, cancel = QPushButton("Save"), QPushButton("Cancel")
        save.setStyleSheet(BTN)
        cancel.setStyleSheet(BTN_GREY)
        save.clicked.connect(self.save)
        cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(save)
        v.addLayout(row)

    def filter(self, text):
        t = text.lower().strip()
        for i, p in enumerate(self.prods):
            self.table.setRowHidden(i, bool(t) and t not in p["name"].lower())

    def save(self):
        try:
            for pid, sp in self.spins.items():
                new = None if sp.value() < 0 else sp.value()
                if new != self.levels.get(pid):
                    extras.set_location_level(self.con, self.gid, pid, new)
            self.con.commit()
        except Exception as e:
            self.con.rollback()
            return QMessageBox.warning(self, "Reorder levels", f"Could not save:\n{e}")
        self.accept()


class LabelsDialog(QDialog):
    def __init__(self, con, product_id=None, parent=None):
        super().__init__(parent)
        self.con = con
        self.setWindowTitle("Print barcode labels")
        self.resize(820, 640)
        v = QVBoxLayout(self)
        note = QLabel("Set how many labels you want for each product. Products without a barcode are given one automatically, "
                      "so the scanner can find them when billing.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search product...")
        self.search.setStyleSheet(INPUT)
        self.search.textChanged.connect(self.load)
        stock = QPushButton("Labels = stock in hand")
        stock.setStyleSheet(BTN_GREY)
        stock.clicked.connect(self.fill_stock)
        clear = QPushButton("Clear")
        clear.setStyleSheet(BTN_GREY)
        clear.clicked.connect(self.clear_all)
        top.addWidget(self.search, 1)
        top.addWidget(stock)
        top.addWidget(clear)
        v.addLayout(top)
        self.table = _table(["Product", "Barcode", "MRP", "Price", "Labels"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        self.counts = {}                                   # product id -> copies (kept while searching)
        self.rows = []

        f = QFormLayout()
        self.layout_box = QComboBox()
        self.layout_box.setStyleSheet(INPUT)
        self.layout_box.addItems(list(labels.LAYOUTS))
        s = db.get_settings(con)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findText(s.get("label_layout", ""))))
        self.price = QComboBox()
        self.price.setStyleSheet(INPUT)
        for text, key in (("Show MRP", "mrp"), ("Show our selling price", "price"), ("No price", "none")):
            self.price.addItem(text, key)
        self.price.setCurrentIndex(max(0, self.price.findData(s.get("label_price", "mrp"))))
        self.shop = QCheckBox("Print shop name on the label")
        self.shop.setChecked(s.get("label_shop", "1") == "1")
        self.skip = QSpinBox()
        self.skip.setRange(0, 200)
        self.skip.setToolTip("Labels already used on a partly used sheet")
        self.dx, self.dy = QDoubleSpinBox(), QDoubleSpinBox()
        for d, key in ((self.dx, "label_dx"), (self.dy, "label_dy")):
            d.setRange(-10, 10)
            d.setSingleStep(0.5)
            d.setSuffix(" mm")
            d.setValue(float(s.get(key) or 0))
        for w in (self.skip, self.dx, self.dy):
            w.setStyleSheet(INPUT)
        f.addRow("Label paper", self.layout_box)
        f.addRow("Price on label", self.price)
        f.addRow("", self.shop)
        f.addRow("Skip labels at the start of the sheet", self.skip)
        f.addRow("Shift print right / down", self._pair(self.dx, self.dy))
        v.addLayout(f)
        self.total = QLabel()
        self.total.setStyleSheet("font-weight:700;")
        v.addWidget(self.total)
        row = QHBoxLayout()
        for text, fn, style in (("Preview", self.preview, BTN_GREY), ("Print", self.print_it, BTN),
                                ("Save PDF", self.save_pdf, BTN_GREY), ("Close", self.accept, BTN_GREY)):
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.table.cellChanged.connect(lambda *_: None)
        if product_id:
            self.counts[product_id] = 1
        self.load()

    @staticmethod
    def _pair(a, b):
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(b)
        return w

    def load(self, *_):
        self.rows = labels.products_for_labels(self.con, self.search.text().strip())
        self.table.setRowCount(len(self.rows))
        self.spins = {}
        for i, r in enumerate(self.rows):
            for c, val in enumerate([r["name"], r["barcode"] or "(auto)", f"{r['mrp']:,.2f}", f"{r['price']:,.2f}"]):
                it = QTableWidgetItem(val)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, c, it)
            sp = QSpinBox()
            sp.setRange(0, 5000)
            sp.setValue(self.counts.get(r["id"], 0))
            sp.valueChanged.connect(lambda val, pid=r["id"]: self.set_count(pid, val))
            self.table.setCellWidget(i, 4, sp)
            self.spins[r["id"]] = sp
        self.update_total()

    def set_count(self, pid, val):
        self.counts[pid] = val
        self.update_total()

    def update_total(self):
        n = sum(self.counts.values())
        self.total.setText(f"{n} label{'s' if n != 1 else ''} to print")

    def fill_stock(self):
        for r in labels.products_for_labels(self.con):
            self.counts[r["id"]] = max(int(r["stock"]), 0)
        self.load()

    def clear_all(self):
        self.counts = {}
        self.load()

    def items(self):
        out, bad = [], []
        for r in labels.products_for_labels(self.con):
            n = self.counts.get(r["id"], 0)
            if n <= 0:
                continue
            code = labels.ensure_barcode(self.con, r["id"])
            try:
                labels.encode(code)
            except ValueError as e:
                bad.append(f"{r['name']}: {e}")
                continue
            out.append(({"name": r["name"], "barcode": code, "mrp": r["mrp"], "price": r["price"]}, n))
        if bad:
            QMessageBox.warning(self, "Labels", "These were left out:\n\n" + "\n".join(bad))
        return out

    def opts(self):
        return {"shop": db.get_settings(self.con)["shop_name"] if self.shop.isChecked() else "", "price": self.price.currentData(),
                "skip": self.skip.value(), "dx": self.dx.value(), "dy": self.dy.value()}

    def remember(self):
        db.save_settings(self.con, {"label_layout": self.layout_box.currentText(), "label_price": self.price.currentData(),
                                    "label_shop": "1" if self.shop.isChecked() else "0", "label_dx": str(self.dx.value()),
                                    "label_dy": str(self.dy.value())})

    def printer(self):
        p = QPrinter(QPrinter.HighResolution)
        labels.configure(p, self.layout_box.currentText())
        return p

    def ready(self):
        its = self.items()
        if not its:
            QMessageBox.information(self, "Labels", "Set the number of labels for at least one product.")
            return None
        return its

    def preview(self):
        its = self.ready()
        if not its:
            return
        dlg = QPrintPreviewDialog(self.printer(), self)
        dlg.paintRequested.connect(lambda pr: labels.paint(pr, self.layout_box.currentText(), its, self.opts()))
        dlg.resize(900, 700)
        dlg.exec()

    def print_it(self):
        its = self.ready()
        if not its:
            return
        pr = self.printer()
        if QPrintDialog(pr, self).exec() != QPrintDialog.Accepted:
            return
        labels.paint(pr, self.layout_box.currentText(), its, self.opts())
        self.remember()
        self.accept()

    def save_pdf(self):
        its = self.ready()
        if not its:
            return
        start = Path.home() / "Documents"
        path, _ = QFileDialog.getSaveFileName(self, "Save labels as PDF", str((start if start.exists() else Path.home()) / "labels.pdf"),
                                              "PDF files (*.pdf)")
        if not path:
            return
        self.write_pdf(path, its)
        self.remember()
        QMessageBox.information(self, "Saved", f"Saved to:\n{path}")

    def write_pdf(self, path, its):
        w = QPdfWriter(path)
        labels.configure(w, self.layout_box.currentText())
        w.setResolution(300)
        labels.paint(w, self.layout_box.currentText(), its, self.opts())

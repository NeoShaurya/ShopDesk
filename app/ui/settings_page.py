"""Shop details (printed on every bill) and print options."""
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QKeySequenceEdit, QCheckBox, QDoubleSpinBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .. import db, printing, shortcuts, theme
from ..gst import clean_gstin, valid_gstin
from ..theme import BTN, BTN_GREY, INPUT
from .gst_rates import GstRatesDialog
from .currency_dialog import CurrencyDialog
from .series_dialog import SeriesDialog


class SettingsPage(QWidget):
    shortcuts_changed = Signal()

    def __init__(self, con):
        super().__init__()
        self.con = con
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        title = QLabel("Settings")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        self.f = {k: QLineEdit() for k in ("shop_name", "shop_address", "shop_phone", "shop_gstin", "thanks_msg")}
        self.paper = QComboBox()
        for key, (label, *_rest) in printing.PAPERS.items():
            self.paper.addItem(label, key)
        self.incl = QCheckBox("My selling prices already include GST")
        form = QFormLayout()
        form.setSpacing(12)
        for label, key in [("Shop name", "shop_name"), ("Address", "shop_address"), ("Phone", "shop_phone"),
                           ("GSTIN (printed on every bill)", "shop_gstin"), ("Thank-you message", "thanks_msg")]:
            self.f[key].setStyleSheet(INPUT)
            self.f[key].setMinimumWidth(420)
            form.addRow(label, self.f[key])
        self.paper.setStyleSheet(INPUT)
        form.addRow("Bill paper", self.paper)
        form.addRow("", self.incl)
        self.open = {}
        for key, label in (("opening_cash", "Opening cash in hand"), ("opening_bank", "Opening bank balance")):
            b = QDoubleSpinBox()
            b.setRange(-1_000_000_000, 1_000_000_000)
            b.setPrefix("₹ ")
            b.setStyleSheet(INPUT)
            b.setToolTip("What you had before your first entry in ShopDesk. Used by the cash book, bank book and balance sheet.")
            self.open[key] = b
            form.addRow(label, b)
        self.credit = QComboBox()
        self.credit.setStyleSheet(INPUT)
        self.credit.addItem("Warn me, and let me continue", "warn")
        self.credit.addItem("Block the bill", "block")
        form.addRow("When a customer goes over their credit limit", self.credit)
        self.dark = QCheckBox("Dark mode")
        self.dark.setChecked(db.get_settings(con)["dark_mode"] == "1")
        self.dark.toggled.connect(self.dark_toggled)
        form.addRow("Appearance", self.dark)
        v.addLayout(form)
        row = QHBoxLayout()
        save = QPushButton("Save settings")
        save.setStyleSheet(BTN)
        save.clicked.connect(self.save)
        prev = QPushButton("Preview a sample bill")
        prev.setStyleSheet(BTN_GREY)
        prev.clicked.connect(self.sample)
        rates_btn = QPushButton("GST rates...")
        rates_btn.setStyleSheet(BTN_GREY)
        rates_btn.clicked.connect(lambda: GstRatesDialog(self.con, self).exec())
        series_btn = QPushButton("Invoice series...")
        series_btn.setStyleSheet(BTN_GREY)
        series_btn.clicked.connect(lambda: SeriesDialog(self.con, self).exec())
        cur_btn = QPushButton("Currencies...")
        cur_btn.setStyleSheet(BTN_GREY)
        cur_btn.clicked.connect(lambda: CurrencyDialog(self.con, self).exec())
        self.status = QLabel()
        self.status.setStyleSheet("color:#16a34a;")
        for w in (save, prev, rates_btn, series_btn, cur_btn, self.status):
            row.addWidget(w)
        row.addStretch()
        v.addLayout(row)

        h2 = QLabel("Keyboard shortcuts")
        h2.setStyleSheet("font-size:16px;font-weight:700;color:#0f172a;margin-top:18px;")
        v.addWidget(h2)
        sf = QFormLayout()
        sf.setSpacing(8)
        self.keys = {}
        for aid, label, _d in shortcuts.ACTIONS:
            e = QKeySequenceEdit()
            e.setMaximumSequenceLength(1)
            e.setClearButtonEnabled(True)
            e.setStyleSheet(INPUT)
            e.setMaximumWidth(240)
            self.keys[aid] = e
            sf.addRow(label.replace("&", "&&"), e)
        v.addLayout(sf)
        r2 = QHBoxLayout()
        sk, rk = QPushButton("Save shortcuts"), QPushButton("Reset to standard")
        sk.setStyleSheet(BTN)
        rk.setStyleSheet(BTN_GREY)
        sk.clicked.connect(self.save_keys)
        rk.clicked.connect(self.reset_keys)
        self.key_status = QLabel()
        for w in (sk, rk, self.key_status):
            r2.addWidget(w)
        r2.addStretch()
        v.addLayout(r2)
        v.addStretch()
        self.refresh()

    def refresh(self):
        s = db.get_settings(self.con)
        for k, w in self.f.items():
            w.setText(s[k])
        self.paper.setCurrentIndex(max(0, self.paper.findData(s["paper"])))
        self.incl.setChecked(s["tax_inclusive"] == "1")
        self.credit.setCurrentIndex(max(0, self.credit.findData(s.get("credit_limit_action"))))
        for k, b in self.open.items():
            b.setValue(float(s.get(k) or 0))
        self.status.setText("")
        self.dark.blockSignals(True)
        self.dark.setChecked(s["dark_mode"] == "1")
        self.dark.blockSignals(False)
        self.show_keys(shortcuts.current(self.con))
        self.key_status.setText("")

    def dark_toggled(self, on):
        win = self.window()
        if hasattr(win, "set_dark") and on != theme.is_dark():
            win.set_dark(on)

    def show_keys(self, mapping):
        for aid, e in self.keys.items():
            e.setKeySequence(QKeySequence(mapping.get(aid, "")))

    def save_keys(self):
        mapping = {a: e.keySequence().toString(QKeySequence.PortableText) for a, e in self.keys.items()}
        try:
            shortcuts.save(self.con, mapping)
        except ValueError as ex:
            self.key_status.setStyleSheet("color:#dc2626;")
            return self.key_status.setText(str(ex))
        self.key_status.setStyleSheet("color:#16a34a;")
        self.key_status.setText("Saved.")
        self.shortcuts_changed.emit()

    def reset_keys(self):
        self.show_keys(shortcuts.DEFAULTS)
        self.save_keys()

    def values(self):
        d = {k: w.text().strip() for k, w in self.f.items()}
        d["paper"] = self.paper.currentData()
        d["tax_inclusive"] = "1" if self.incl.isChecked() else "0"
        d["credit_limit_action"] = self.credit.currentData()
        for k, b in self.open.items():
            d[k] = str(round(b.value(), 2))
        return d

    def save(self):
        d = self.values()
        d["shop_gstin"] = clean_gstin(d["shop_gstin"])
        self.f["shop_gstin"].setText(d["shop_gstin"])
        db.save_settings(self.con, d)
        bad = d["shop_gstin"] and not valid_gstin(d["shop_gstin"])
        self.status.setStyleSheet("color:#d97706;" if bad else "color:#16a34a;")
        self.status.setText("Saved - but this GSTIN does not look like a valid 15-character GSTIN. Please check it."
                            if bad else "Saved.")

    def sample(self):
        shop = self.values()
        sale = {"invoice_no": "INV-00001", "sale_date": datetime.now().isoformat(sep=" "), "customer_name": "Sample Customer",
                "payment_mode": "cash", "subtotal": 200.0, "tax": 24.0, "discount": 0.0, "total": 224.0, "paid": 224.0,
                "tax_inclusive": 0}
        items = [{"name": "Sample product A", "qty": 2, "unit_price": 50.0, "gst_percent": 12.0},
                 {"name": "Sample product B", "qty": 1, "unit_price": 100.0, "gst_percent": 12.0}]
        printing.preview_html(printing.invoice_html(shop, sale, items, shop["paper"]), shop["paper"], self)

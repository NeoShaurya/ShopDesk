"""Settings > GST rates: which rates products may use, and a tool to move many products to a new rate at once."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout)

from .. import db, gst
from ..theme import BTN, BTN_GREY, INPUT


class GstRatesDialog(QDialog):
    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.con = con
        self.setWindowTitle("GST rates")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setSpacing(10)

        h1 = QLabel("Allowed GST rates")
        h1.setStyleSheet("font-size:15px;font-weight:700;")
        v.addWidget(h1)
        v.addWidget(QLabel("Products can only use these rates. Separate them with commas."))
        self.rates = QLineEdit(gst.fmt_rates(gst.rates(con)))
        self.rates.setStyleSheet(INPUT)
        row = QHBoxLayout()
        row.addWidget(self.rates, 1)
        save = QPushButton("Save rates")
        save.setStyleSheet(BTN)
        save.clicked.connect(self.save_rates)
        row.addWidget(save)
        v.addLayout(row)
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)

        h2 = QLabel("Change the rate on many products at once")
        h2.setStyleSheet("font-size:15px;font-weight:700;margin-top:14px;")
        v.addWidget(h2)
        v.addWidget(QLabel("Use this when the government changes a rate. Old bills keep the rate they were made with."))
        form = QFormLayout()
        self.old, self.new, self.cat = QComboBox(), QComboBox(), QComboBox()
        for b in (self.old, self.new, self.cat):
            b.setStyleSheet(INPUT)
            b.currentIndexChanged.connect(self.count)
        self.keep = QCheckBox("Keep what the customer pays the same (changes my selling prices)")
        self.keep.setToolTip("Only for shops whose selling prices do not include GST.")
        form.addRow("Products now at", self.old)
        form.addRow("Change them to", self.new)
        form.addRow("Only in category", self.cat)
        form.addRow("", self.keep)
        v.addLayout(form)
        self.info = QLabel()
        self.info.setStyleSheet("color:#64748b;")
        v.addWidget(self.info)
        row2 = QHBoxLayout()
        self.go = QPushButton("Change rate")
        self.go.setStyleSheet(BTN)
        self.go.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.setStyleSheet(BTN_GREY)
        close.clicked.connect(self.accept)
        row2.addWidget(self.go)
        row2.addStretch()
        row2.addWidget(close)
        v.addLayout(row2)
        self.load()

    def load(self):
        con = self.con
        used = [r[0] for r in con.execute("SELECT DISTINCT gst_percent FROM products WHERE active=1 ORDER BY 1")]
        self.old.blockSignals(True)
        self.old.clear()
        for r in used:
            self.old.addItem(f"{r:g}%", r)
        self.old.blockSignals(False)
        self.new.blockSignals(True)
        self.new.clear()
        for r in gst.rates(con):
            self.new.addItem(f"{r:g}%", r)
        self.new.blockSignals(False)
        self.cat.blockSignals(True)
        self.cat.clear()
        self.cat.addItem("All categories", None)
        for r in con.execute("SELECT DISTINCT category FROM products WHERE active=1 AND IFNULL(category,'') != '' ORDER BY 1"):
            self.cat.addItem(r[0], r[0])
        self.cat.blockSignals(False)
        inclusive = db.get_settings(con)["tax_inclusive"] == "1"
        self.keep.setEnabled(not inclusive)
        if inclusive:
            self.keep.setChecked(False)
            self.keep.setText("Your selling prices include GST, so the customer's price stays the same automatically")
        self.count()

    def count(self, *_):
        old = self.old.currentData()
        n = 0 if old is None else self.con.execute(
            "SELECT COUNT(*) FROM products WHERE active=1 AND gst_percent=? AND (? IS NULL OR category=?)",
            (old, self.cat.currentData(), self.cat.currentData())).fetchone()[0]
        self.n = n
        self.info.setText(f"{n} product(s) will change." if n else "No products match.")
        self.go.setEnabled(n > 0 and old is not None and self.new.currentData() != old)

    def save_rates(self):
        try:
            vals = gst.parse_rates(self.rates.text())
        except ValueError as e:
            self.msg.setStyleSheet("color:#dc2626;")
            return self.msg.setText(str(e))
        db.save_settings(self.con, {"gst_rates": gst.fmt_rates(vals)})
        self.rates.setText(gst.fmt_rates(vals))
        stranded = [f"{r[0]:g}%" for r in self.con.execute(
            "SELECT DISTINCT gst_percent FROM products WHERE active=1 ORDER BY 1") if r[0] not in vals]
        self.msg.setStyleSheet("color:#16a34a;")
        self.msg.setText("Saved." + (f" Some products are still on {', '.join(stranded)}. Move them below; until then "
                                     "they cannot be edited without choosing a new rate." if stranded else ""))
        self.load()

    def apply(self):
        old, new = self.old.currentData(), self.new.currentData()
        text = f"Change {self.n} product(s) from {old:g}% to {new:g}% GST?"
        if self.keep.isChecked():
            text += "\n\nTheir selling prices will be adjusted so customers pay the same total."
        if QMessageBox.question(self, "Change GST rate", text + "\n\nThis cannot be undone in one click (it is recorded in the Audit Log).") \
                != QMessageBox.Yes:
            return
        n = db.bulk_change_gst(self.con, old, new, self.cat.currentData(), self.keep.isChecked())
        self.msg.setStyleSheet("color:#16a34a;")
        self.msg.setText(f"{n} product(s) moved to {new:g}%.")
        self.load()

"""Payment fields shared by Billing and Purchases:
method, advance (paid now), advance method (only when needed) and balance due."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QWidget

from ..theme import INPUT

METHODS = [("Cash", "cash"), ("UPI", "upi"), ("Card", "card"), ("Credit", "credit"), ("Mixed", "mixed")]
PAID_NOW = ("cash", "upi", "card")      # these settle the bill at once; credit / mixed leave a balance


def rupees(v):
    return f"₹ {v:,.2f}"


class PaymentBox(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.total = 0.0
        self._auto = True                  # paid amount follows the total until the user edits it
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(10)
        self.method = QComboBox()
        self.method.setStyleSheet(INPUT)
        for label, key in METHODS:
            self.method.addItem(label, key)
        self.paid = QDoubleSpinBox()
        self.paid.setRange(0, 10_000_000)
        self.paid.setPrefix("₹ ")
        self.paid.setStyleSheet(INPUT)
        self.via_label = QLabel("Advance paid via")
        self.via = QComboBox()
        self.via.setStyleSheet(INPUT)
        for label in ("Cash", "UPI", "Card"):
            self.via.addItem(label, label.lower())
        self.due = QLabel()
        g.addWidget(QLabel("Payment method"), 0, 0)
        g.addWidget(self.method, 0, 1)
        g.addWidget(QLabel("Advance / paid now"), 1, 0)
        g.addWidget(self.paid, 1, 1)
        g.addWidget(self.via_label, 2, 0)
        g.addWidget(self.via, 2, 1)
        g.addWidget(self.due, 3, 0, 1, 2)
        self.method.currentIndexChanged.connect(self._method_changed)
        self.paid.valueChanged.connect(self._paid_changed)
        self.via.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self._refresh()

    def set_total(self, total):
        self.total = round(total, 2)
        self.paid.blockSignals(True)
        self.paid.setMaximum(max(self.total, 0.01))
        if self._auto and self.method.currentData() in PAID_NOW:
            self.paid.setValue(self.total)
        self.paid.blockSignals(False)
        self._refresh()

    def _method_changed(self, *_):
        self._auto = self.method.currentData() in PAID_NOW
        self.paid.blockSignals(True)
        self.paid.setValue(self.total if self._auto else 0)
        self.paid.blockSignals(False)
        self._refresh()

    def _paid_changed(self, *_):
        self._auto = False
        self._refresh()

    def _refresh(self):
        due = self.due_amount()
        show_via = self.paid.value() > 0 and self.method.currentData() not in PAID_NOW
        self.via_label.setVisible(show_via)
        self.via.setVisible(show_via)
        if due > 0.005:
            self.due.setText(f"Balance due   {rupees(due)}")
            self.due.setStyleSheet("color:#dc2626;font-weight:700;font-size:15px;")
        else:
            self.due.setText("Fully paid" if self.total > 0 else "")
            self.due.setStyleSheet("color:#16a34a;font-weight:600;")
        self.changed.emit()

    def due_amount(self):
        return round(self.total - min(self.paid.value(), self.total), 2)

    def values(self):
        """(payment method, advance paid now, advance method or None)"""
        m, paid = self.method.currentData(), min(self.paid.value(), self.total)
        if paid <= 0:
            via = None
        else:
            via = m if m in PAID_NOW else self.via.currentData()
        return m, paid, via

    def reset(self):
        self.method.blockSignals(True)
        self.method.setCurrentIndex(0)
        self.method.blockSignals(False)
        self._auto = True
        self.set_total(self.total)

    def state(self):
        return {"method": self.method.currentData(), "paid": self.paid.value(), "via": self.via.currentData()}

    def restore(self, st):
        """Put back a saved payment choice (call after the bill total has been set)."""
        self.method.blockSignals(True)
        self.method.setCurrentIndex(max(0, self.method.findData(st.get("method"))))
        self.method.blockSignals(False)
        self.via.setCurrentIndex(max(0, self.via.findData(st.get("via"))))
        self._auto = False
        self.paid.setValue(min(st.get("paid", 0), max(self.total, 0.01)))
        self._refresh()

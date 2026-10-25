"""Print Cheque: fill in payee / amount / date, preview, print. Plus the editor for bank cheque layouts."""
import copy
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QPdfWriter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from .. import cheque, db
from ..theme import BTN, BTN_GREY, INPUT


class ChequeDialog(QDialog):
    def __init__(self, con, payee="", amount=0.0, party_id=None, payment_id=None, cheque_no="", when=None, parent=None):
        super().__init__(parent)
        self.con, self.party_id, self.payment_id = con, party_id, payment_id
        self.setWindowTitle("Print cheque")
        self.setMinimumWidth(520)
        self.templates = cheque.load_templates(con)
        v = QVBoxLayout(self)
        f = QFormLayout()
        f.setSpacing(8)
        self.bank = QComboBox()
        self.bank.setStyleSheet(INPUT)
        self.fill_banks(db.get_settings(con)["cheque_template"])
        self.payee = QLineEdit(payee)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, 999_999_999)
        self.amount.setDecimals(2)
        self.amount.setPrefix("₹ ")
        self.amount.setValue(amount or 0.01)
        self.date = QDateEdit(QDate.fromString(when, "yyyy-MM-dd") if when else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd MMM yyyy")
        self.no = QLineEdit(cheque_no or "")
        self.ac = QCheckBox("Account payee only (crossed cheque)")
        self.ac.setChecked(True)
        for w in (self.payee, self.amount, self.date, self.no):
            w.setStyleSheet(INPUT)
        for label, w in (("Bank layout", self.bank), ("Pay to *", self.payee), ("Amount *", self.amount), ("Cheque date", self.date),
                         ("Cheque no.", self.no), ("", self.ac)):
            f.addRow(label, w)
        v.addLayout(f)
        self.words = QLabel()
        self.words.setWordWrap(True)
        self.words.setStyleSheet("color:#0f172a;background:#f1f5f9;border-radius:6px;padding:8px;")
        v.addWidget(self.words)
        note = QLabel("Check the position on plain paper first (Test alignment), then hold it against your cheque. "
                      "If it is a few millimetres off, adjust the layout in Cheque layouts.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        v.addWidget(note)
        row = QHBoxLayout()
        for text, fn, style in (("Preview", self.preview, BTN_GREY), ("Print", self.print_it, BTN),
                                ("Save PDF", self.save_pdf, BTN_GREY), ("Test alignment", self.test_page, BTN_GREY),
                                ("Cheque layouts...", self.edit_layouts, BTN_GREY)):
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.amount.valueChanged.connect(self.show_words)
        self.show_words()

    def fill_banks(self, current=None):
        self.bank.clear()
        for name in self.templates:
            self.bank.addItem(name)
        self.bank.setCurrentIndex(max(0, self.bank.findText(current or "")))

    def show_words(self, *_):
        self.words.setText(f"<b>{cheque.amount_in_words(self.amount.value())}</b><br>₹ {cheque.indian_number(self.amount.value())}/-")

    def data(self):
        return {"payee": self.payee.text().strip(), "amount": round(self.amount.value(), 2), "date": self.date.date().toPython(),
                "cheque_no": self.no.text().strip(), "ac_payee": self.ac.isChecked(), "payment_id": self.payment_id,
                "party_id": self.party_id, "template": self.bank.currentText()}

    def valid(self):
        if not self.payee.text().strip():
            QMessageBox.warning(self, "Pay to", "Enter who the cheque is payable to.")
            return False
        return True

    def tpl(self):
        return self.templates[self.bank.currentText()]

    def printer(self):
        p = QPrinter(QPrinter.HighResolution)
        cheque.configure(p, self.tpl())
        return p

    def preview(self):
        if not self.valid():
            return
        dlg = QPrintPreviewDialog(self.printer(), self)
        dlg.setWindowTitle("Cheque preview")
        dlg.paintRequested.connect(lambda pr: cheque.paint(pr, self.tpl(), self.data(), guide=True))
        dlg.resize(900, 520)
        dlg.exec()

    def print_it(self):
        if not self.valid():
            return
        pr = self.printer()
        if QPrintDialog(pr, self).exec() != QPrintDialog.Accepted:
            return
        cheque.paint(pr, self.tpl(), self.data())
        d = self.data()
        cheque.log_print(self.con, d)
        db.save_settings(self.con, {"cheque_template": d["template"]})
        self.accept()

    def save_pdf(self):
        if not self.valid():
            return
        start = Path.home() / "Documents"
        path, _ = QFileDialog.getSaveFileName(self, "Save cheque as PDF", str((start if start.exists() else Path.home()) / "cheque.pdf"),
                                              "PDF files (*.pdf)")
        if path:
            self.write_pdf(path, self.data(), guide=False)
            QMessageBox.information(self, "Saved", f"Saved to:\n{path}")

    def write_pdf(self, path, data, guide):
        w = QPdfWriter(path)
        cheque.configure(w, self.tpl())
        w.setResolution(300)
        cheque.paint(w, self.tpl(), data, guide=guide)

    def test_page(self):
        """Prints a sample with the cheque outline, on whatever paper is in the printer."""
        pr = self.printer()
        if QPrintDialog(pr, self).exec() != QPrintDialog.Accepted:
            return
        cheque.paint(pr, self.tpl(), {"payee": "Test Payee Name", "amount": 12345.5, "date": date.today(), "cheque_no": "000000",
                                      "ac_payee": True}, guide=True)

    def edit_layouts(self):
        cur = self.bank.currentText()
        ChequeLayoutsDialog(self.con, self).exec()
        self.templates = cheque.load_templates(self.con)
        self.fill_banks(cur)


FIELD_LABELS = {"date": "Date (first digit)", "payee": "Pay to", "words1": "Amount in words, line 1", "words2": "Amount in words, line 2",
                "amount": "Amount in figures", "acpayee": "A/C Payee crossing", "cheque_no": "Cheque number"}


class ChequeLayoutsDialog(QDialog):
    """Move things on a bank's cheque by editing millimetre positions."""

    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.con = con
        self.setWindowTitle("Cheque layouts")
        self.resize(640, 560)
        self.templates = cheque.load_templates(con)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.bank = QComboBox()
        self.bank.setStyleSheet(INPUT)
        for n in self.templates:
            self.bank.addItem(n)
        self.bank.currentIndexChanged.connect(self.load)
        top.addWidget(QLabel("Bank"))
        top.addWidget(self.bank, 1)
        v.addLayout(top)
        v.addWidget(QLabel("All values are millimetres from the top-left corner of the cheque. "
                           "Nudge the whole layout with the two shift boxes."))
        form = QFormLayout()
        self.dx, self.dy = QDoubleSpinBox(), QDoubleSpinBox()
        for s in (self.dx, self.dy):
            s.setRange(-30, 30)
            s.setDecimals(1)
            s.setSuffix(" mm")
            s.setStyleSheet(INPUT)
        self.paper = QComboBox()
        self.paper.addItem("Cheque-size paper (feed the cheque directly)", "cheque")
        self.paper.addItem("A4 paper (cheque placed at the position below)", "a4")
        self.paper.setStyleSheet(INPUT)
        self.ax, self.ay = QDoubleSpinBox(), QDoubleSpinBox()
        for s in (self.ax, self.ay):
            s.setRange(0, 200)
            s.setDecimals(1)
            s.setSuffix(" mm")
            s.setStyleSheet(INPUT)
        form.addRow("Shift everything right", self.dx)
        form.addRow("Shift everything down", self.dy)
        form.addRow("Paper", self.paper)
        form.addRow("On A4: cheque starts at (from left)", self.ax)
        form.addRow("On A4: cheque starts at (from top)", self.ay)
        v.addLayout(form)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Field", "Print", "X", "Y", "Width", "Font size"])
        self.table.verticalHeader().hide()
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        save = QPushButton("Save")
        save.setStyleSheet(BTN)
        save.clicked.connect(self.save)
        reset = QPushButton("Reset this bank to standard")
        reset.setStyleSheet(BTN_GREY)
        reset.clicked.connect(self.reset)
        close = QPushButton("Close")
        close.setStyleSheet(BTN_GREY)
        close.clicked.connect(self.accept)
        for w in (save, reset):
            row.addWidget(w)
        row.addStretch()
        row.addWidget(close)
        v.addLayout(row)
        self.load()

    def load(self, *_):
        t = self.templates[self.bank.currentText()]
        self.dx.setValue(t["dx"])
        self.dy.setValue(t["dy"])
        self.paper.setCurrentIndex(max(0, self.paper.findData(t["paper"])))
        self.ax.setValue(t["a4_x"])
        self.ay.setValue(t["a4_y"])
        self.table.setRowCount(len(t["fields"]))
        self.keys = list(t["fields"])
        for i, k in enumerate(self.keys):
            c = t["fields"][k]
            name = QTableWidgetItem(FIELD_LABELS.get(k, k))
            name.setFlags(Qt.ItemIsEnabled)
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            on.setCheckState(Qt.Checked if c.get("on", True) else Qt.Unchecked)
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, on)
            for j, key in enumerate(("x", "y", "w", "size"), 2):
                self.table.setItem(i, j, QTableWidgetItem(f"{c.get(key, 0):g}"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def save(self):
        t = copy.deepcopy(self.templates[self.bank.currentText()])
        try:
            for i, k in enumerate(self.keys):
                c = t["fields"][k]
                c["on"] = self.table.item(i, 1).checkState() == Qt.Checked
                for j, key in enumerate(("x", "y", "w", "size"), 2):
                    c[key] = float(self.table.item(i, j).text())
        except ValueError:
            return QMessageBox.warning(self, "Check the numbers", "Positions and sizes must be numbers.")
        t.update(dx=self.dx.value(), dy=self.dy.value(), paper=self.paper.currentData(), a4_x=self.ax.value(), a4_y=self.ay.value())
        self.templates[self.bank.currentText()] = t
        cheque.save_templates(self.con, self.templates)
        QMessageBox.information(self, "Saved", "Layout saved.")

    def reset(self):
        name = self.bank.currentText()
        self.templates[name] = cheque.default_templates().get(name, cheque._base())
        cheque.save_templates(self.con, self.templates)
        self.load()

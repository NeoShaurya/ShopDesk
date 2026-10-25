"""Export to Tally: choose the period, check the counts, save the two XML files to import into Tally."""
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QAbstractItemView, QDateEdit, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import tally
from ..theme import BTN, BTN_GREY, INPUT, TABLE

ORDER = ["Sales", "Credit Note", "Purchase", "Debit Note", "Receipt", "Payment"]


class TallyPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.b = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("Export to Tally")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)
        sub = QLabel("Creates files with your bills, purchases, returns and payments that Tally can import.")
        sub.setStyleSheet("color:#64748b;")
        v.addWidget(sub)

        today = QDate.currentDate()
        first = QDate(today.year(), today.month(), 1)
        self.d1, self.d2 = QDateEdit(first), QDateEdit(today)
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd MMM yyyy")
            d.setStyleSheet(INPUT)
            d.dateChanged.connect(self.preview)
        row = QHBoxLayout()
        for w in (QLabel("From"), self.d1, QLabel("To"), self.d2):
            row.addWidget(w)
        row.addStretch()
        v.addLayout(row)

        o = tally.load_opts(con)
        self.f = {}
        form = QFormLayout()
        form.setSpacing(8)
        for key, label in (("company", "Company name in Tally"), ("sales", "Sales ledger"), ("purchase", "Purchase ledger"),
                           ("cash", "Cash ledger"), ("bank", "Bank ledger (UPI, card, cheque, transfer)")):
            e = QLineEdit(o[key])
            e.setStyleSheet(INPUT)
            e.setMaximumWidth(380)
            e.editingFinished.connect(self.preview)
            self.f[key] = e
            form.addRow(label, e)
        v.addLayout(form)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Vouchers", "Count"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setStyleSheet(TABLE)
        self.table.setMaximumHeight(230)
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#92400e;")
        v.addWidget(self.note)
        btn = QPushButton("Export for Tally")
        btn.setStyleSheet(BTN)
        btn.clicked.connect(self.export)
        row2 = QHBoxLayout()
        row2.addWidget(btn)
        row2.addStretch()
        v.addLayout(row2)
        how = QLabel("In Tally, open your company, then Import > Data. Import the file called <b>1_Ledgers</b> first, "
                     "then <b>2_Vouchers</b>. Ledgers that already exist are skipped. Your Tally GST settings decide how "
                     "GST returns are filed from there; the amounts and tax ledgers come from ShopDesk.")
        how.setWordWrap(True)
        how.setStyleSheet("color:#64748b;")
        v.addWidget(how)
        v.addStretch()
        self.preview()

    def refresh(self):
        self.preview()

    def opts(self):
        o = {k: e.text().strip() or tally.DEFAULT_OPTS[k] for k, e in self.f.items()}
        o["company"] = self.f["company"].text().strip() or tally.load_opts(self.con)["company"]
        return o

    def dates(self):
        return self.d1.date().toString("yyyy-MM-dd"), self.d2.date().toString("yyyy-MM-dd")

    def preview(self, *_):
        try:
            self.b = tally.build(self.con, *self.dates(), self.opts())
        except ValueError as e:
            self.b = None
            self.note.setText(str(e))
            return self.table.setRowCount(0)
        rows = [(t, self.b.counts.get(t, 0)) for t in ORDER]
        self.table.setRowCount(len(rows) + 1)
        for i, (t, n) in enumerate(rows + [("Total", sum(n for _, n in rows))]):
            self.table.setItem(i, 0, QTableWidgetItem(t))
            self.table.setItem(i, 1, QTableWidgetItem(str(n)))
        self.note.setText("\n".join(self.b.warnings))

    def export(self):
        self.preview()
        if self.b is None or not self.b.vouchers:
            return QMessageBox.information(self, "Nothing to export", "There are no bills, purchases or payments in these dates.")
        tally.save_opts(self.con, self.opts())
        start = Path.home() / "Documents"
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for the Tally files",
                                                  str(start if start.exists() else Path.home()))
        if not folder:
            return
        try:
            mp, vp = tally.write_files(self.b, folder)
        except OSError as e:
            return QMessageBox.warning(self, "Could not save", str(e))
        QMessageBox.information(self, "Exported", f"{len(self.b.vouchers)} vouchers saved:\n\n{mp.name}\n{vp.name}\n\nin {folder}")

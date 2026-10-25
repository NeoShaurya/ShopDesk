"""GST Reports screen: pick a month, generate GSTR-1 or GSTR-3B, check the preview, then export."""
import calendar
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import db, gstr, printing
from ..export import save_dialog
from .. import theme
from ..theme import BTN, BTN_GREY, INPUT, TABLE


class GstReports(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rep = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        title = QLabel("GST Reports")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        bar = QHBoxLayout()
        today = date.today()
        m, y = (today.month - 1, today.year) if today.month > 1 else (12, today.year - 1)   # default: last month
        self.month = QComboBox()
        self.month.setStyleSheet(INPUT)
        for i in range(1, 13):
            self.month.addItem(calendar.month_name[i], i)
        self.month.setCurrentIndex(m - 1)
        self.year = QSpinBox()
        self.year.setStyleSheet(INPUT)
        self.year.setRange(2017, 2100)
        self.year.setValue(y)
        b1, b3 = QPushButton("Generate GSTR-1"), QPushButton("Generate GSTR-3B")
        b1.setStyleSheet(BTN)
        b3.setStyleSheet(BTN)
        b1.clicked.connect(lambda: self.generate("gstr1"))
        b3.clicked.connect(lambda: self.generate("gstr3b"))
        for w in (QLabel("Month"), self.month, QLabel("Year"), self.year, b1, b3):
            bar.addWidget(w)
        bar.addStretch()
        self.printbar = printing.PrintBar(self.get_doc)
        self.csv_btn, self.xl_btn = QPushButton("Export CSV"), QPushButton("Export Excel")
        for b in (self.csv_btn, self.xl_btn):
            b.setStyleSheet(BTN_GREY)
        self.csv_btn.clicked.connect(self.export_csv)
        self.xl_btn.clicked.connect(self.export_xlsx)
        for w in (self.printbar, self.csv_btn, self.xl_btn):
            bar.addWidget(w)
        v.addLayout(bar)

        self.sub = QLabel("Choose a month and press Generate GSTR-1 or Generate GSTR-3B.")
        self.sub.setStyleSheet("color:#64748b;")
        v.addWidget(self.sub)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        v.addWidget(self.scroll, 1)
        self._set_enabled(False)

    def _set_enabled(self, on):
        for w in (self.printbar, self.csv_btn, self.xl_btn):
            w.setEnabled(on)

    def generate(self, kind):
        y, m = self.year.value(), self.month.currentData()
        self.rep = gstr.gstr1(self.con, y, m) if kind == "gstr1" else gstr.gstr3b(self.con, y, m)
        self.sub.setText(f"{self.rep['title']}  -  {self.rep['subtitle']}")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(8)
        for w in self.rep["warnings"]:
            lb = QLabel("⚠ " + w)
            lb.setWordWrap(True)
            lb.setStyleSheet("background:#fffbeb;color:#92400e;border:1px solid #fcd34d;border-radius:6px;padding:6px 10px;")
            lay.addWidget(lb)
        for sec in self.rep["sections"]:
            h = QLabel(sec["heading"])
            h.setStyleSheet("font-size:15px;font-weight:700;color:#0f172a;margin-top:8px;")
            lay.addWidget(h)
            lay.addWidget(self._table(sec))
            if sec["note"]:
                n = QLabel(sec["note"])
                n.setWordWrap(True)
                n.setStyleSheet("color:#64748b;font-size:12px;")
                lay.addWidget(n)
        for t in self.rep["notes"]:
            n = QLabel("• " + t)
            n.setWordWrap(True)
            n.setStyleSheet("color:#64748b;font-size:12px;")
            lay.addWidget(n)
        lay.addStretch()
        self.scroll.setWidget(body)
        self._set_enabled(True)

    def _table(self, sec):
        rows = sec["rows"] + sec["footer"]
        t = QTableWidget(len(rows), len(sec["headers"]))
        t.setHorizontalHeaderLabels(sec["headers"])
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.verticalHeader().hide()
        t.setAlternatingRowColors(True)
        t.setStyleSheet(TABLE)
        for r, cells in enumerate(rows):
            foot = r >= len(sec["rows"])
            for c, val in enumerate(cells):
                it = QTableWidgetItem(str(val))
                if sec["aligns"][c] == "r":
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if foot:
                    f = QFont(it.font())
                    f.setBold(True)
                    it.setFont(f)
                    it.setBackground(QBrush(QColor(theme.footer_bg())))
                t.setItem(r, c, it)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.resizeRowsToContents()
        h = t.horizontalHeader().height() + 4 + sum(t.rowHeight(i) for i in range(len(rows))) + (24 if not rows else 0)
        t.setFixedHeight(h)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return t

    def get_doc(self):
        shop = db.get_settings(self.con)
        return printing.gstr_html(self.rep, shop), "a4", self.rep["prefix"]

    def export_xlsx(self):
        save_dialog(self, gstr.sections_to_reps(self.rep), self.rep["prefix"])

    def export_csv(self):
        start = Path.home() / "Documents"
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for the CSV files",
                                                  str(start if start.exists() else Path.home()))
        if not folder:
            return
        try:
            paths = gstr.write_csvs(self.rep, folder)
        except OSError as e:
            return QMessageBox.warning(self, "Could not save", str(e))
        QMessageBox.information(self, "Exported", f"{len(paths)} CSV files saved in:\n{folder}")

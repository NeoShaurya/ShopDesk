"""Main window: left sidebar + stacked pages. Add new screens by appending to self.pages."""
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QListWidget, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut

from .. import alerts, db, drafts, pdc, shortcuts, theme
from ..backup import backup_database
from .alerts_page import AlertsPage
from .billing import Billing
from .dashboard import Dashboard
from .inventory import Inventory
from .parties import PartiesPage
from .pricing import PricingPage
from .purchases import PurchasePage
from .reports_page import Reports
from .search_bar import SearchBar
from .invoice_dialog import InvoiceDialog
from .gst_page import GstReports
from .audit_page import AuditPage
from .tally_page import TallyPage
from .pdc_page import PdcPage
from .expenses_page import ExpensesPage
from .fy_page import FyPage
from .freeze_page import FreezePage
from .returns import ReturnsPage
from .settings_page import SettingsPage
from .stock import StockPage


class MainWindow(QMainWindow):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.setWindowTitle("ShopDesk")
        self.resize(1280, 800)

        self.dashboard = Dashboard(con)
        self.pages = [
            ("Home", self.dashboard),
            ("Billing", Billing(con)),
            ("Purchases", PurchasePage(con)),
            ("Returns", ReturnsPage(con)),
            ("Inventory", Inventory(con)),
            ("Stock In / Out", StockPage(con)),
            ("Customers & Suppliers", PartiesPage(con)),
            ("PDC Register", PdcPage(con)),
            ("Expenses", ExpensesPage(con)),
            ("Pricing & Offers", PricingPage(con)),
            ("Reports", Reports(con)),
            ("GST Reports", GstReports(con)),
            ("Export to Tally", TallyPage(con)),
            ("Financial Years", FyPage(con)),
            ("Freeze Entries", FreezePage(con)),
            ("Audit Log", AuditPage(con)),
            ("Alerts", AlertsPage(con)),
            ("Settings", SettingsPage(con)),
        ]

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.stack = QStackedWidget()
        for name, w in self.pages:
            self.nav.addItem(name)
            self.stack.addWidget(w)
        self.nav.currentRowChanged.connect(self.switch)
        self.nav.setCurrentRow(0)

        self.backup_btn = QPushButton("Backup database")
        self.backup_btn.setStyleSheet(
            "QPushButton{background:#1e293b;color:#e2e8f0;border:none;padding:14px 20px;font-size:14px;text-align:left;}"
            "QPushButton:hover{background:#334155;}")
        self.backup_btn.clicked.connect(self.backup_now)

        side = QWidget()
        side.setFixedWidth(210)
        side.setObjectName("sidebar")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        sv.addWidget(self.nav, 1)
        sv.addWidget(self.backup_btn)
        side.setStyleSheet("background:#0f1729;")

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.search = SearchBar(con, root)
        self.search.chosen.connect(self.open_result)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self.search)
        rv.addWidget(self.stack, 1)
        h.addWidget(side)
        h.addWidget(right, 1)
        self.setCentralWidget(root)

        self._shortcuts = []
        self.bind_shortcuts()
        self.page("Settings").shortcuts_changed.connect(self.bind_shortcuts)

        self._alert_worker = None
        self._alert_timer = QTimer(self)                  # check for the daily expiry reminder
        self._alert_timer.timeout.connect(self.check_alerts)
        self._alert_timer.timeout.connect(lambda: self.check_pdcs() if self._draft_timer.isActive() else None)
        self._alert_timer.start(30 * 60 * 1000)
        QTimer.singleShot(5000, self.check_alerts)

        self._draft_timer = QTimer(self)                  # keep unsaved bills / purchases safe
        self._draft_timer.timeout.connect(self.autosave_drafts)
        QTimer.singleShot(400, self.restore_drafts)       # after the window is on screen

    def switch(self, i):
        self.stack.setCurrentIndex(i)
        page = self.stack.widget(i)
        if hasattr(page, "refresh"):
            page.refresh()            # always show fresh data when a page is opened
        if hasattr(page, "focus_first"):
            page.focus_first()

    DRAFTS = (("bill", "Billing", "bill"), ("purchase", "Purchases", "purchase entry"))

    def autosave_drafts(self):
        for _kind, name, _label in self.DRAFTS:
            try:
                self.page(name).autosave()
            except sqlite3.Error:
                pass                                       # never interrupt the user over a draft

    def restore_drafts(self):
        """Offer to bring back work that was not saved last time. Auto-saving starts only afterwards."""
        for kind, name, label in self.DRAFTS:
            found = drafts.load(self.con, kind)
            if not found:
                continue
            data, when = found
            page = self.page(name)
            at = datetime.fromisoformat(when).strftime("%d %b, %I:%M %p")
            box = QMessageBox(QMessageBox.Question, "Unsaved work",
                              f"You have an unsaved {label} from {at} ({page.draft_summary(data)}).\n\nRestore it?",
                              parent=self)
            yes = box.addButton("Restore", QMessageBox.AcceptRole)
            box.addButton("Discard", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is yes:
                try:
                    page.restore_draft(data)
                    self.goto(name)
                except Exception as e:
                    QMessageBox.warning(self, "Could not restore", f"The saved {label} could not be restored:\n{e}")
                    drafts.clear(self.con, kind)
            else:
                drafts.clear(self.con, kind)
        self._draft_timer.start(30_000)
        self.check_pdcs()

    def check_pdcs(self):
        """Once a day: tell the owner which cheques have reached their date."""
        from datetime import date
        today = date.today().isoformat()
        if db.get_settings(self.con).get("pdc_alerted") == today:
            return
        text = pdc.alert_text(self.con)
        if not text:
            return
        db.save_settings(self.con, {"pdc_alerted": today})
        box = QMessageBox(QMessageBox.Information, "Cheques due", text, parent=self)
        open_btn = box.addButton("Open PDC Register", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self.goto("PDC Register")

    def toggle_dark(self):
        self.set_dark(not theme.is_dark())

    def set_dark(self, on):
        theme.set_dark(QApplication.instance(), on)
        db.save_settings(self.con, {"dark_mode": "1" if on else "0"})
        page = self.stack.currentWidget()                  # redraw what is on screen with the new colours
        if hasattr(page, "refresh"):
            page.refresh()
        self.page("Settings").dark.setChecked(on)
        self.update()

    def closeEvent(self, ev):
        if self._draft_timer.isActive():                   # not while the restore question is still unanswered
            self.autosave_drafts()
        super().closeEvent(ev)

    def page(self, name):
        return next(w for n, w in self.pages if n == name)

    def bind_shortcuts(self):
        """(Re)creates every keyboard shortcut from the saved choices."""
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        billing = self.page("Billing")

        def on_billing(fn):
            return lambda: fn() if self.stack.currentWidget() is billing else None

        acts = {"search": self.search.focus, "backup": self.backup_now,
                "go_home": lambda: self.goto("Home"), "go_billing": lambda: self.goto("Billing"),
                "go_purchases": lambda: self.goto("Purchases"), "go_inventory": lambda: self.goto("Inventory"),
                "go_parties": lambda: self.goto("Customers & Suppliers"), "go_reports": lambda: self.goto("Reports"),
                "save_bill": on_billing(lambda: billing.save(False)),
                "save_print_bill": on_billing(lambda: billing.save(True)),
                "dark_mode": self.toggle_dark}
        for aid, key in shortcuts.current(self.con).items():
            if key and aid in acts:
                sc = QShortcut(QKeySequence(key), self)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(acts[aid])
                self._shortcuts.append(sc)

    def goto(self, name):
        for i, (n, _) in enumerate(self.pages):
            if n == name:
                self.nav.setCurrentRow(i)
                return self.pages[i][1]

    def open_result(self, kind, rid):
        """Jump to the thing picked in the search box."""
        self.search.edit.clear()
        if kind == "invoice":
            return InvoiceDialog(self.con, rid, self).exec()
        if kind in ("customer", "supplier"):
            page = self.goto("Customers & Suppliers")
            row = db.get_party(self.con, rid)
            (page.tab_c if kind == "customer" else page.tab_s).setChecked(True)
            page.switch(kind)
            page.search.setText(row["name"])
            return
        page = self.goto("Inventory")
        if kind == "product":
            page.search.setText(db.get_product(self.con, rid)["name"])
        else:
            page.search.setText(db.get_batch(self.con, rid)["batch_no"])

    def check_alerts(self):
        if self._alert_worker is not None and self._alert_worker.isRunning():
            return
        self._alert_worker = alerts.start_if_due(self.con, self)
        if self._alert_worker is not None:
            self._alert_worker.done.connect(lambda ok, text: alerts.record_result(self.con, ok, text))

    def backup_now(self):
        start = db.get_settings(self.con).get("backup_dir") or str(Path.home() / "Documents")
        folder = QFileDialog.getExistingDirectory(self, "Choose where to save the backup (USB drive, "
                                                  "Google Drive folder, ...)", start)
        if not folder:
            return
        try:
            path = backup_database(self.con, folder)
        except (OSError, sqlite3.Error) as e:
            return QMessageBox.warning(self, "Backup failed", f"Could not save the backup:\n{e}\n\n"
                                       "Check that the drive is connected and has free space, then try again.")
        db.save_settings(self.con, {"backup_dir": folder})
        QMessageBox.information(self, "Backup saved", f"Backup saved to:\n{path}\n\n"
                                f"Size: {path.stat().st_size / 1024:,.0f} KB")

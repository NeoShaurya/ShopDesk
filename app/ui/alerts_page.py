"""Alerts screen: expiry reminders by email / SMS / WhatsApp."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import alerts, db, share
from ..theme import BTN, BTN_GREY, INPUT

FIELDS = ("alert_email_to", "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from",
          "alert_sms_url", "alert_sms_numbers", "alert_wa_number")


class AlertsPage(QWidget):
    def __init__(self, con):
        super().__init__()
        self.con = con
        self.worker = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)
        title = QLabel("Expiry alerts")
        title.setStyleSheet("font-size:24px;font-weight:700;color:#0f172a;")
        v.addWidget(title)

        top = QGroupBox("When to remind")
        f = QFormLayout(top)
        self.enabled = QCheckBox("Send a reminder automatically once a day (while ShopDesk is open)")
        self.days = QComboBox()
        self.days.setStyleSheet(INPUT)
        for n in (7, 15, 30):
            self.days.addItem(f"{n} days before expiry", n)
        f.addRow(self.enabled)
        f.addRow("Remind about stock expiring within", self.days)
        v.addWidget(top)

        self.f = {k: QLineEdit() for k in FIELDS}
        for w in self.f.values():
            w.setStyleSheet(INPUT)
        self.f["smtp_pass"].setEchoMode(QLineEdit.Password)
        self.f["alert_sms_url"].setPlaceholderText("https://your-sms-provider/send?to={number}&text={message}")
        em = QGroupBox("Email")
        f = QFormLayout(em)
        for label, key in [("Send reminders to", "alert_email_to"), ("Mail server", "smtp_host"), ("Port", "smtp_port"),
                           ("Username", "smtp_user"), ("Password", "smtp_pass"), ("From address", "smtp_from")]:
            f.addRow(label, self.f[key])
        note = QLabel("Gmail: server smtp.gmail.com, port 587, and an App password (Google Account > Security > "
                      "App passwords), not your normal password. The password is stored on this computer only, "
                      "unencrypted.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;")
        f.addRow(note)
        v.addWidget(em)

        sm = QGroupBox("SMS")
        f = QFormLayout(sm)
        f.addRow("SMS provider link", self.f["alert_sms_url"])
        f.addRow("Phone numbers (comma separated)", self.f["alert_sms_numbers"])
        n2 = QLabel("SMS needs an account with an SMS provider. Paste the 'send SMS' web link they give you and put "
                    "{number} and {message} where the phone number and text go. I could not test this with a real "
                    "provider, so send a test first.")
        n2.setWordWrap(True)
        n2.setStyleSheet("color:#64748b;")
        f.addRow(n2)
        v.addWidget(sm)

        wa = QGroupBox("WhatsApp (no account needed)")
        f = QFormLayout(wa)
        f.addRow("Your WhatsApp number", self.f["alert_wa_number"])
        v.addWidget(wa)

        row = QHBoxLayout()
        for text, fn, style in [("Save settings", self.save, BTN), ("Send a test message", self.test, BTN_GREY),
                                ("Send expiry reminder now", self.send_now, BTN_GREY),
                                ("Open reminder in WhatsApp", self.whatsapp, BTN_GREY)]:
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        v.addStretch()
        self.refresh()

    def refresh(self):
        s = db.get_settings(self.con)
        self.enabled.setChecked(s["alert_enabled"] == "1")
        self.days.setCurrentIndex(max(0, self.days.findData(int(s["alert_days"]))))
        for k, w in self.f.items():
            w.setText(s[k])
        self.show_last()

    def show_last(self, text=None):
        last = text or db.get_settings(self.con)["alert_last_result"]
        self.status.setText(("Last result: " + last) if last else "No reminder has been sent yet.")
        self.status.setStyleSheet("color:#dc2626;" if "FAILED" in (last or "") else "color:#475569;")

    def values(self):
        d = {k: w.text().strip() for k, w in self.f.items()}
        d["alert_enabled"] = "1" if self.enabled.isChecked() else "0"
        d["alert_days"] = str(self.days.currentData())
        return d

    def save(self):
        db.save_settings(self.con, self.values())
        self.show_last("settings saved.")

    def _send(self, msg):
        if self.worker and self.worker.isRunning():
            return
        self.save()
        self.show_last("sending...")
        self.worker = alerts.AlertWorker(db.get_settings(self.con), msg, self)
        self.worker.done.connect(self._sent)
        self.worker.start()

    def _sent(self, ok, text):
        self.show_last(("sent by " + text) if ok else "FAILED - " + text)
        if ok:
            alerts.record_result(self.con, True, text)

    def test(self):
        self._send({"subject": "ShopDesk test message", "body": "This is a test message from ShopDesk. "
                    "If you can read this, expiry reminders will reach you.", "sms": "ShopDesk test message."})

    def send_now(self):
        msg = alerts.build_message(self.con, self.days.currentData())
        if msg is None:
            return QMessageBox.information(self, "Expiry reminder", "Nothing is expired or expiring in that window.")
        self._send(msg)

    def whatsapp(self):
        msg = alerts.build_message(self.con, self.days.currentData())
        if msg is None:
            return QMessageBox.information(self, "Expiry reminder", "Nothing is expired or expiring in that window.")
        cfg = db.get_settings(self.con)
        share.open_whatsapp(share.normalize_phone(self.f["alert_wa_number"].text(), cfg["country_code"]), msg["body"])

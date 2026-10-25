"""Expiry reminders by email and SMS, sent automatically once a day while ShopDesk is open."""
import smtplib
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from urllib.parse import quote

from PySide6.QtCore import QThread, Signal

from . import db


def build_message(con, days):
    """{'subject','body','sms'} for everything expired or expiring within `days`, or None if there is nothing."""
    shop = db.get_settings(con)["shop_name"]
    n, value = db.expiring(con, days)
    if n == 0:
        return None
    rows = db.expiring_list(con, days, 40)
    lines = [f"{shop} - expiry reminder", f"{n} batch(es) expired or expiring within {days} days, "
             f"stock worth Rs {value:,.0f} at cost.", ""]
    for r in rows:
        tag = "EXPIRED" if r["days_left"] < 0 else "expires today" if r["days_left"] == 0 else f"{r['days_left']} days left"
        lines.append(f"- {r['name']} (batch {r['batch_no']}): {r['qty']} in stock, {r['expiry_date']}, {tag}")
    if n > len(rows):
        lines.append(f"...and {n - len(rows)} more. See Reports > Expiry report in ShopDesk.")
    return {"subject": f"{shop}: {n} batch(es) expiring within {days} days", "body": "\n".join(lines),
            "sms": f"{shop}: {n} batches expired/expiring within {days} days (Rs {value:,.0f}). Check ShopDesk expiry report."}


def send_email(cfg, subject, body):
    if not (cfg["smtp_host"] and cfg["alert_email_to"]):
        raise ValueError("Email settings are incomplete.")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, cfg["alert_email_to"]
    msg["From"] = cfg["smtp_from"] or cfg["smtp_user"] or cfg["alert_email_to"]
    msg.set_content(body)
    port = int(cfg["smtp_port"] or 587)
    smtp = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=15) if port == 465 else smtplib.SMTP(cfg["smtp_host"], port, timeout=15)
    with smtp:
        smtp.ehlo()
        secure = port == 465
        if not secure and smtp.has_extn("starttls"):
            smtp.starttls()
            smtp.ehlo()
            secure = True
        if cfg["smtp_user"]:
            if not secure:                       # never send a password over an unencrypted connection
                raise RuntimeError("the mail server does not offer an encrypted connection, so the password was not sent")
            smtp.login(cfg["smtp_user"], cfg["smtp_pass"])
        smtp.send_message(msg)


def send_sms(cfg, text):
    """Calls your SMS provider's web link once per number. {number} and {message} in the link are replaced."""
    url_t = cfg["alert_sms_url"]
    numbers = [n.strip() for n in cfg["alert_sms_numbers"].replace(";", ",").split(",") if n.strip()]
    if not (url_t and numbers):
        raise ValueError("SMS settings are incomplete.")
    for n in numbers:
        url = url_t.replace("{number}", quote(n)).replace("{message}", quote(text[:300]))
        with urllib.request.urlopen(url, timeout=15) as r:
            if not 200 <= r.status < 300:
                raise RuntimeError(f"SMS provider answered {r.status}")


def send_alert(cfg, msg):
    """Sends on every channel that is set up. Returns a short result text; raises only if nothing worked."""
    ok, bad = [], []
    if cfg["smtp_host"] and cfg["alert_email_to"]:
        try:
            send_email(cfg, msg["subject"], msg["body"])
            ok.append("email")
        except Exception as e:
            bad.append(f"email failed: {e}")
    if cfg["alert_sms_url"] and cfg["alert_sms_numbers"]:
        try:
            send_sms(cfg, msg["sms"])
            ok.append("SMS")
        except Exception as e:
            bad.append(f"SMS failed: {e}")
    if not ok and not bad:
        raise ValueError("No email or SMS is set up yet (Alerts screen).")
    if not ok:
        raise RuntimeError("; ".join(bad))
    return ", ".join(ok) + ("; " + "; ".join(bad) if bad else "")


class AlertWorker(QThread):
    """Sends in the background so the app never freezes on a slow connection."""
    done = Signal(bool, str)

    def __init__(self, cfg, msg, parent=None):
        super().__init__(parent)
        self.cfg, self.msg = dict(cfg), msg

    def run(self):
        try:
            self.done.emit(True, send_alert(self.cfg, self.msg))
        except Exception as e:
            self.done.emit(False, str(e))


def start_if_due(con, parent=None, force=False):
    """Called every so often by the main window. Returns a started AlertWorker, or None if nothing to send."""
    cfg = db.get_settings(con)
    if not force:
        if cfg["alert_enabled"] != "1" or cfg["alert_last_sent"] == date.today().isoformat():
            return None
        try:
            if datetime.now() - datetime.fromisoformat(cfg["alert_last_try"]) < timedelta(minutes=55):
                return None                                   # failed recently: don't hammer the server
        except ValueError:
            pass
    msg = build_message(con, int(cfg["alert_days"]))
    if msg is None:
        db.save_settings(con, {"alert_last_sent": date.today().isoformat(),
                               "alert_last_result": f"{datetime.now():%d %b %H:%M}: nothing is expiring"})
        return None
    db.save_settings(con, {"alert_last_try": datetime.now().isoformat(timespec="seconds")})
    worker = AlertWorker(cfg, msg, parent)
    worker.start()
    return worker


def record_result(con, ok, text):
    now = datetime.now()
    if ok:
        db.save_settings(con, {"alert_last_sent": date.today().isoformat(),
                               "alert_last_result": f"{now:%d %b %H:%M}: sent by {text}"})
    else:
        db.save_settings(con, {"alert_last_result": f"{now:%d %b %H:%M}: FAILED - {text}"})

"""WhatsApp sharing. A normal (free) WhatsApp account cannot be sent files by another program, so the bill is
saved as a PDF, the PDF is put on the clipboard, and WhatsApp opens on the right chat with the message typed in.
The person only has to paste the file and press send."""
import re
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

from . import printing


def normalize_phone(raw, country="91"):
    """Digits with country code, e.g. '98765 43210' -> '919876543210'. '' if it can't be a phone number."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 10:
        return country + d
    if len(d) == 11 and d.startswith("0"):
        return country + d[1:]
    return d if len(d) >= 11 else ""


def whatsapp_url(phone, text):
    return f"https://wa.me/{phone}?text={quote(text)}" if phone else f"https://wa.me/?text={quote(text)}"


def open_whatsapp(phone, text):
    QDesktopServices.openUrl(QUrl(whatsapp_url(phone, text)))


def bills_folder():
    docs = Path.home() / "Documents"
    folder = (docs if docs.exists() else Path.home()) / "ShopDesk Bills"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def share_pdf(html_text, paper, name, phone, message, country="91"):
    """Save the PDF, copy it to the clipboard, open WhatsApp. Returns the PDF path."""
    path = bills_folder() / f"{name}.pdf"
    printing.render_pdf(html_text, paper, path)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    QGuiApplication.clipboard().setMimeData(mime)
    open_whatsapp(normalize_phone(phone, country), message)
    return path

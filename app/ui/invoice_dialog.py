"""On-screen copy of a printable document with Print / Preview / Save PDF.
Used for bills, credit notes and debit notes."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QTextBrowser,
                               QVBoxLayout)

from .. import db, extras, printing, share
from ..theme import BTN, BTN_GREY


def invoice_doc(con, sale_id):
    """(html, paper, file name) for a saved bill, using the current shop settings."""
    shop = db.get_settings(con)
    sale = db.get_sale(con, sale_id)
    paper = shop["paper"]
    return printing.invoice_html(shop, sale, db.get_sale_items(con, sale_id), paper), paper, sale["invoice_no"]


def note_doc(con, note_id):
    """(html, paper, file name) for a credit / debit note."""
    shop = db.get_settings(con)
    note = db.get_note(con, note_id)
    paper = shop["paper"]
    return printing.note_html(shop, note, db.get_note_items(con, note_id), paper), paper, note["note_no"]


class DocDialog(QDialog):
    def __init__(self, get_doc, label, parent=None, autoprint=False, phone="", share_text="", con=None, frozen=False):
        super().__init__(parent)
        self.get_doc, self.phone, self.share_text, self.con = get_doc, phone, share_text, con
        html_text, paper, name = get_doc()
        self.setWindowTitle(f"{label} {name}")
        self.resize(360 if paper != "a4" else 760, 660)
        v = QVBoxLayout(self)
        self.head = head = QLabel(f"{label} {name}" + ("   🔒 Frozen" if frozen else ""))
        head.setStyleSheet("font-size:18px;font-weight:700;color:#16a34a;")
        v.addWidget(head)
        self.view = view = QTextBrowser()
        view.setHtml(html_text)
        v.addWidget(view, 1)
        self.row = row = QHBoxLayout()
        self.bar = printing.PrintBar(get_doc)
        row.addWidget(self.bar)
        if share_text:
            wa = QPushButton("Send on WhatsApp")
            wa.setStyleSheet("QPushButton{background:white;color:#15803d;border:1px solid #86efac;border-radius:6px;"
                             "padding:8px 16px;}QPushButton:hover{background:#f0fdf4;}")
            wa.clicked.connect(self.send_whatsapp)
            row.addWidget(wa)
        row.addStretch()
        close = QPushButton("Done")
        close.setStyleSheet(BTN)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        if autoprint:
            QTimer.singleShot(250, self.bar.do_print)

    def send_whatsapp(self):
        phone = self.phone
        if not share.normalize_phone(phone):
            phone, ok = QInputDialog.getText(self, "WhatsApp", "Customer's WhatsApp number (10 digits):")
            if not ok:
                return
            if not share.normalize_phone(phone):
                return QMessageBox.warning(self, "WhatsApp", "That doesn't look like a phone number.")
            self.phone = phone
        html_text, paper, name = self.get_doc()
        country = db.get_settings(self.con)["country_code"] if self.con else "91"
        path = share.share_pdf(html_text, paper, name, phone, self.share_text, country)
        QMessageBox.information(self, "WhatsApp", f"WhatsApp is opening with your message.\n\nThe PDF is saved here:\n{path}\n\n"
                                "It has also been copied: paste it into the chat to attach it, then press send.")


def _phone_of(con, party_id):
    p = db.get_party(con, party_id) if party_id else None
    return (p["phone"] or "") if p else ""


class InvoiceDialog(DocDialog):
    def __init__(self, con, sale_id, parent=None, autoprint=False):
        sale = db.get_sale(con, sale_id)
        shop = db.get_settings(con)["shop_name"]
        text = (f"Hello {sale['customer_name']}, thank you for shopping at {shop}. Your bill {sale['invoice_no']} for "
                f"Rs {sale['total']:,.2f} is attached." + (f" Balance due: Rs {sale['balance_due']:,.2f}." if sale["balance_due"] > 0.005 else ""))
        super().__init__(lambda: invoice_doc(con, sale_id), "Bill", parent, autoprint,
                         _phone_of(con, sale["customer_id"]), text, con, bool(sale["frozen"]))
        self.sale_id = sale_id
        self.cancel_btn = QPushButton("Cancel bill")
        self.cancel_btn.setStyleSheet(BTN_GREY)
        self.cancel_btn.clicked.connect(self.cancel_bill)
        self.row.insertWidget(1, self.cancel_btn)
        self.update_state()

    def update_state(self):
        sale = db.get_sale(self.con, self.sale_id)
        if sale["cancelled"]:
            self.head.setText(f"Bill {sale['invoice_no']}   CANCELLED")
            self.head.setStyleSheet("font-size:18px;font-weight:700;color:#dc2626;")
        self.cancel_btn.setVisible(not sale["cancelled"])

    def cancel_bill(self):
        sale = db.get_sale(self.con, self.sale_id)
        reason, ok = QInputDialog.getText(self, "Cancel bill", f"Why is bill {sale['invoice_no']} being cancelled?\n\n"
                                          "Stock goes back and the customer's balance is reduced. The bill stays in the records "
                                          "marked CANCELLED, and its number is not reused.")
        if not ok:
            return
        try:
            extras.cancel_sale(self.con, self.sale_id, reason)
        except ValueError as e:
            return QMessageBox.warning(self, "Cancel bill", str(e))
        if (sale["paid"] or 0) > 0:
            QMessageBox.information(self, "Cancel bill", f"Bill cancelled.\n\nThe customer paid ₹{sale['paid']:,.2f} with this bill - "
                                    "remember to hand it back; ShopDesk no longer counts it as money received.")
        self.view.setHtml(self.get_doc()[0])
        self.update_state()

class NoteDialog(DocDialog):
    def __init__(self, con, note_id, parent=None, autoprint=False):
        n = db.get_note(con, note_id)
        shop = db.get_settings(con)["shop_name"]
        kind = "Credit note" if n["kind"] == "customer" else "Debit note"
        text = f"Hello {n['party_name']}, {kind} {n['note_no']} for Rs {n['total']:,.2f} from {shop} is attached."
        super().__init__(lambda: note_doc(con, note_id), kind, parent, autoprint, _phone_of(con, n["party_id"]), text, con, bool(n["frozen"]))

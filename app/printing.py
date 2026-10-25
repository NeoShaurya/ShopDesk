"""Printing: bills and reports become HTML -> QTextDocument -> printer / preview / PDF.
Nothing here knows about the database; it just turns data into pages."""
import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF
from PySide6.QtGui import QFont, QGuiApplication, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QWidget)

# key: (label, page size, margin in mm, base font pt)
PAPERS = {
    "a4":  ("A4 (normal printer)", QPageSize(QPageSize.A4), 14, 10),
    "t80": ("Thermal receipt 80 mm", QPageSize(QSizeF(80, 297), QPageSize.Millimeter, "Receipt80"), 3, 8),
    "t58": ("Thermal receipt 58 mm", QPageSize(QSizeF(58, 297), QPageSize.Millimeter, "Receipt58"), 2, 7),
}


def money(v):
    return f"{v:,.2f}"


def _get(row, key):
    return row[key] if key in row.keys() and row[key] is not None else ""


def esc(s):
    return html.escape(str(s if s is not None else ""))


# ---------------------------------------------------------------- HTML builders

def invoice_html(shop, sale, items, paper="a4"):
    thermal = paper != "a4"
    inclusive = bool(sale["tax_inclusive"])
    big, small = ("12pt", "7pt") if thermal else ("20pt", "9pt")

    head = f'<div align="center"><b style="font-size:{big}">{esc(shop["shop_name"])}</b>'
    for line in (shop["shop_address"], f'Ph: {shop["shop_phone"]}' if shop["shop_phone"] else "",
                 f'GSTIN: {shop["shop_gstin"]}' if shop["shop_gstin"] else ""):
        if line:
            head += f'<br><span style="font-size:{small}">{esc(line)}</span>'
    title = "TAX INVOICE" if shop["shop_gstin"] else "BILL"
    head += f'</div><hr><div align="center"><b>{title}</b></div>'
    if _get(sale, "cancelled"):
        head += '<div align="center"><b style="color:#dc2626">*** CANCELLED ***</b></div>'
    head += '<br>'

    cg = _get(sale, "customer_gstin")
    cg_row = f'<tr><td><b>Customer GSTIN:</b> {esc(cg)}</td></tr>' if cg else ""
    cg_br = f'<br><b>Customer GSTIN:</b> {esc(cg)}' if cg else ""
    when = datetime.fromisoformat(sale["sale_date"]).strftime("%d %b %Y, %I:%M %p")
    meta = (f'<table width="100%" cellspacing="0" cellpadding="1">'
            f'<tr><td><b>Bill no:</b> {esc(sale["invoice_no"])}</td></tr>'
            f'<tr><td><b>Date:</b> {when}</td></tr>'
            f'<tr><td><b>Customer:</b> {esc(sale["customer_name"])}</td></tr>{cg_row}</table><br>') if thermal else (
            f'<table width="100%" cellspacing="0" cellpadding="2"><tr>'
            f'<td><b>Bill no:</b> {esc(sale["invoice_no"])}<br><b>Customer:</b> {esc(sale["customer_name"])}{cg_br}</td>'
            f'<td align="right"><b>Date:</b> {when}<br><b>Payment:</b> {esc(sale["payment_mode"]).title()}</td>'
            f'</tr></table><br>')

    def amount(i):                       # what the customer pays for this line (before the invoice-level discount)
        return i["qty"] * i["unit_price"] - (_get(i, "discount") or 0)

    any_disc = any((_get(i, "discount") or 0) > 0 for i in items)
    if thermal:
        def sub(i):
            d = _get(i, "discount") or 0
            return f'{i["qty"]} x {money(i["unit_price"])}' + (f'  (disc -{money(d)})' if d else "")
        rows = "".join(
            f'<tr><td>{esc(i["name"])}<br><span style="font-size:{small}">{sub(i)}</span></td>'
            f'<td align="right" valign="bottom" style="white-space:nowrap">{money(amount(i))}</td></tr>' for i in items)
        table = (f'<table width="100%" cellspacing="0" cellpadding="2">'
                 f'<tr bgcolor="#dddddd"><td><b>Item</b></td><td align="right"><b>Amount</b></td></tr>{rows}</table><hr>')
    else:
        rows = "".join(
            f'<tr bgcolor="{"#f6f8fa" if n % 2 else "#ffffff"}"><td>{n}</td><td>{esc(i["name"])}</td>'
            f'<td>{esc(_get(i, "hsn_code"))}</td><td align="right">{i["qty"]}</td>'
            f'<td align="right">{money(i["unit_price"])}</td>'
            + (f'<td align="right">{money(_get(i, "discount") or 0) if (_get(i, "discount") or 0) else "-"}</td>' if any_disc else "")
            + f'<td align="right">{i["gst_percent"]:g}%</td><td align="right">{money(amount(i))}</td></tr>'
            for n, i in enumerate(items, 1))
        table = ('<table width="100%" cellspacing="0" cellpadding="6">'
                 '<tr bgcolor="#e2e8f0"><td><b>#</b></td><td><b>Item</b></td><td><b>HSN</b></td>'
                 '<td align="right"><b>Qty</b></td><td align="right"><b>Rate</b></td>'
                 + ('<td align="right"><b>Disc</b></td>' if any_disc else "")
                 + f'<td align="right"><b>GST</b></td><td align="right"><b>Amount</b></td></tr>{rows}</table><br>')

    def line(label, value, bold=False, size=None):
        st = f' style="font-size:{size}"' if size else ""
        b1, b2 = ("<b>", "</b>") if bold else ("", "")
        return (f'<tr><td align="right"{st}>{b1}{label}{b2}</td>'
                f'<td align="right" style="white-space:nowrap;{"" if not size else "font-size:" + size}">'
                f'{b1}{value}{b2}</td></tr>')

    t = ""
    if inclusive:
        t += line("Items total", money(sale["subtotal"] + sale["tax"]))
    else:
        t += line("Subtotal", money(sale["subtotal"]))
        if sale["tax"]:
            t += line("GST", money(sale["tax"]))
    if sale["discount"]:
        t += line("Discount", "- " + money(sale["discount"]))
    base = shop.get("base_currency") or "INR"
    t += line("TOTAL", ("" if thermal else ("₹ " if base == "INR" else base + " ")) + money(sale["total"]), bold=True, size="13pt" if not thermal else "9pt")
    cur = _get(sale, "currency")
    if cur and cur != base and _get(sale, "orig_total"):
        t += line(f"Total in {cur}", f"{cur} {sale['orig_total']:,.2f}", bold=True)
        t += line("Exchange rate", f"1 {cur} = {sale['exchange_rate']:g} {base}", size=small)
    if inclusive and sale["tax"]:
        t += line("(includes GST", money(sale["tax"]) + ")", size=small)
    if sale["total"] - sale["paid"] > 0.005:
        t += line("Paid", money(sale["paid"]))
        t += line("Balance due", money(sale["total"] - sale["paid"]), bold=True)
    totals = f'<table width="100%" cellspacing="0" cellpadding="3">{t}</table>'

    thanks = (f'<hr><div align="center"><br>{esc(shop["thanks_msg"])}</div>' if shop["thanks_msg"] else "")
    return f"<html><body>{head}{meta}{table}{totals}{thanks}</body></html>"


def note_html(shop, note, items, paper="a4"):
    """Credit note (customer return) or debit note (supplier return)."""
    thermal = paper != "a4"
    credit = note["kind"] == "customer"
    title = "CREDIT NOTE" if credit else "DEBIT NOTE"
    big, small = ("12pt", "7pt") if thermal else ("20pt", "9pt")
    head = f'<div align="center"><b style="font-size:{big}">{esc(shop["shop_name"])}</b>'
    for line in (shop["shop_address"], f'Ph: {shop["shop_phone"]}' if shop["shop_phone"] else "",
                 f'GSTIN: {shop["shop_gstin"]}' if shop["shop_gstin"] else ""):
        if line:
            head += f'<br><span style="font-size:{small}">{esc(line)}</span>'
    head += f'</div><hr><div align="center"><b>{title}</b></div><br>'

    when = datetime.fromisoformat(note["created_at"]).strftime("%d %b %Y, %I:%M %p")
    party = ("Customer" if credit else "Supplier")
    gstin = f'<br><b>{party} GSTIN:</b> {esc(note["party_gstin"])}' if note["party_gstin"] else ""
    reason = f'<br><b>Reason:</b> {esc(note["reason"])}' if note["reason"] else ""
    meta = (f'<table width="100%" cellspacing="0" cellpadding="2"><tr>'
            f'<td><b>{"Credit" if credit else "Debit"} note no:</b> {esc(note["note_no"])}<br>'
            f'<b>Against {"invoice" if credit else "purchase"}:</b> {esc(note["ref_no"])}<br>'
            f'<b>{party}:</b> {esc(note["party_name"])}{gstin}{reason}</td>'
            + ('' if thermal else f'<td align="right" valign="top"><b>Date:</b> {when}</td>')
            + '</tr></table>' + (f'<div>{when}</div>' if thermal else '') + '<br>')

    if thermal:
        rows = "".join(
            f'<tr><td>{esc(i["name"])}<br><span style="font-size:{small}">{i["qty"]} x {money(i["unit_price"])}'
            f' + {i["gst_percent"]:g}% GST</span></td>'
            f'<td align="right" valign="bottom" style="white-space:nowrap">{money(i["line_total"])}</td></tr>' for i in items)
        table = (f'<table width="100%" cellspacing="0" cellpadding="2"><tr bgcolor="#dddddd"><td><b>Item</b></td>'
                 f'<td align="right"><b>Amount</b></td></tr>{rows}</table><hr>')
    else:
        rows = "".join(
            f'<tr bgcolor="{"#f6f8fa" if n % 2 else "#ffffff"}"><td>{n}</td><td>{esc(i["name"])}</td>'
            f'<td>{esc(_get(i, "hsn_code"))}</td><td align="right">{i["qty"]}</td>'
            f'<td align="right">{money(i["unit_price"])}</td><td align="right">{money(i["taxable_value"])}</td>'
            f'<td align="right">{i["gst_percent"]:g}%</td><td align="right">{money(i["gst_amount"])}</td>'
            f'<td align="right">{money(i["line_total"])}</td></tr>' for n, i in enumerate(items, 1))
        table = ('<table width="100%" cellspacing="0" cellpadding="6"><tr bgcolor="#e2e8f0"><td><b>#</b></td>'
                 '<td><b>Item</b></td><td><b>HSN</b></td><td align="right"><b>Qty</b></td>'
                 '<td align="right"><b>Rate</b></td><td align="right"><b>Taxable</b></td>'
                 '<td align="right"><b>GST</b></td><td align="right"><b>GST amt</b></td>'
                 f'<td align="right"><b>Total</b></td></tr>{rows}</table><br>')

    def line(label, value, bold=False, size=None):
        st = f' style="font-size:{size}"' if size else ""
        b1, b2 = ("<b>", "</b>") if bold else ("", "")
        return (f'<tr><td align="right"{st}>{b1}{label}{b2}</td><td align="right" '
                f'style="white-space:nowrap;{"font-size:" + size if size else ""}">{b1}{value}{b2}</td></tr>')

    method = note["refund_method"]
    settle = ("Adjusted against the " + ("customer's" if credit else "supplier's") + " account balance"
              if method == "adjust" else
              ("Refunded to customer by " if credit else "Refund received from supplier by ") + method.upper())
    totals = ('<table width="100%" cellspacing="0" cellpadding="3">'
              + line("Taxable value", money(note["taxable_value"])) + line("GST reversed", money(note["gst_amount"]))
              + line("TOTAL", ("" if thermal else "₹ ") + money(note["total"]), True, "9pt" if thermal else "13pt")
              + "</table>")
    goods = ""
    if credit and not note["restocked"]:
        goods = f'<div><i>Goods not taken back into stock.</i></div>'
    return (f'<html><body>{head}{meta}{table}{totals}<br><div align="{"center" if thermal else "left"}">'
            f'<b>Settlement:</b> {esc(settle)}</div>{goods}</body></html>')


def report_html(rep, shop):
    n = len(rep["headers"])
    th = "".join(f'<td align="{"right" if a == "r" else "left"}"><b>{esc(h)}</b></td>'
                 for h, a in zip(rep["headers"], rep["aligns"]))

    def tr(cells, bg, bold=False):
        b1, b2 = ("<b>", "</b>") if bold else ("", "")
        tds = "".join(f'<td align="{"right" if a == "r" else "left"}">{b1}{esc(c)}{b2}</td>'
                      for c, a in zip(cells, rep["aligns"]))
        return f'<tr bgcolor="{bg}">{tds}</tr>'

    body = "".join(tr(r, "#f6f8fa" if i % 2 else "#ffffff") for i, r in enumerate(rep["rows"]))
    if not rep["rows"]:
        body = f'<tr><td colspan="{n}" align="center"><br>No data for this report.<br></td></tr>'
    foot = "".join(tr(r, "#e2e8f0", bold=True) for r in rep["footer"])
    return (f'<html><body><div><b style="font-size:16pt">{esc(shop["shop_name"])}</b><br>'
            f'<b style="font-size:13pt">{esc(rep["title"])}</b><br>'
            f'<span style="font-size:9pt">{esc(rep["subtitle"])}   |   Printed {datetime.now():%d %b %Y, %I:%M %p}</span></div><hr>'
            f'<table width="100%" cellspacing="0" cellpadding="5"><tr bgcolor="#cbd5e1">{th}</tr>{body}{foot}</table>'
            f'</body></html>')


def gstr_html(rep, shop):
    """GSTR-1 / GSTR-3B summary: every section as its own table, warnings and notes on top / bottom."""
    parts = [f'<html><body><div><b style="font-size:16pt">{esc(shop["shop_name"])}</b>'
             + (f' &nbsp; GSTIN {esc(shop["shop_gstin"])}' if shop.get("shop_gstin") else "") +
             f'<br><b style="font-size:13pt">{esc(rep["title"])}</b><br>'
             f'<span style="font-size:9pt">{esc(rep["subtitle"])}   |   Printed {datetime.now():%d %b %Y, %I:%M %p}</span></div><hr>']
    for w in rep["warnings"]:
        parts.append(f'<div style="color:#b45309">&#9888; {esc(w)}</div>')
    for sec in rep["sections"]:
        n = len(sec["headers"])
        th = "".join(f'<td align="{"right" if a == "r" else "left"}"><b>{esc(h)}</b></td>'
                     for h, a in zip(sec["headers"], sec["aligns"]))

        def tr(cells, bg, bold=False):
            b1, b2 = ("<b>", "</b>") if bold else ("", "")
            return f'<tr bgcolor="{bg}">' + "".join(
                f'<td align="{"right" if a == "r" else "left"}">{b1}{esc(c)}{b2}</td>'
                for c, a in zip(cells, sec["aligns"])) + "</tr>"

        body = "".join(tr(r, "#f6f8fa" if i % 2 else "#ffffff") for i, r in enumerate(sec["rows"]))
        if not sec["rows"]:
            body = f'<tr><td colspan="{n}" align="center">Nothing to report</td></tr>'
        foot = "".join(tr(r, "#e2e8f0", True) for r in sec["footer"])
        parts.append(f'<p><b style="font-size:11pt">{esc(sec["heading"])}</b></p>'
                     f'<table width="100%" cellspacing="0" cellpadding="4"><tr bgcolor="#cbd5e1">{th}</tr>{body}{foot}</table>')
        if sec["note"]:
            parts.append(f'<div style="font-size:8pt;color:#64748b">{esc(sec["note"])}</div>')
    parts.append("<hr>" + "".join(f'<div style="font-size:8pt;color:#64748b">- {esc(x)}</div>' for x in rep["notes"]))
    return "".join(parts) + "</body></html>"


# ---------------------------------------------------------------- rendering
# We lay the document out ourselves in "screen pixels" and scale it onto the printer,
# so sizes are physically correct on any PC and receipts get an exact page height.

def _screen_dpi():
    s = QGuiApplication.primaryScreen()
    return float(round(s.logicalDotsPerInchX())) if s else 96.0


def make_doc(html_text, paper):
    doc = QTextDocument()
    doc.setDefaultFont(QFont("Segoe UI", PAPERS[paper][3]))
    doc.setHtml(html_text)
    return doc


def prepare(html_text, paper):
    """Returns (doc, page_size, margin_mm, page_height_px)."""
    _, size, margin, _pt = PAPERS[paper]
    dpi = _screen_dpi()
    size_mm = size.size(QPageSize.Millimeter)
    doc = make_doc(html_text, paper)
    width_px = (size_mm.width() - 2 * margin) / 25.4 * dpi
    if paper == "a4":                       # paginate normally
        page_h = (size_mm.height() - 2 * margin) / 25.4 * dpi
        doc.setPageSize(QSizeF(width_px, page_h))
        return doc, size, margin, page_h
    doc.setTextWidth(width_px)              # receipt: one page exactly as tall as the bill
    page_h = doc.size().height() + 2
    doc.setPageSize(QSizeF(width_px, page_h))
    height_mm = page_h / dpi * 25.4 + 2 * margin + 6
    return doc, QPageSize(QSizeF(size_mm.width(), height_mm), QPageSize.Millimeter, "Receipt"), margin, page_h


def _paint(device, doc, page_h):
    painter = QPainter(device)
    scale = device.resolution() / _screen_dpi()
    painter.scale(scale, scale)
    width = doc.pageSize().width()
    for i in range(doc.pageCount()):
        if i:
            device.newPage()
        painter.save()
        top = i * page_h
        painter.translate(0, -top)
        doc.drawContents(painter, QRectF(0, top, width, page_h))
        painter.restore()
    painter.end()


def _configure(device, size, margin):
    device.setPageSize(size)
    device.setPageMargins(QMarginsF(margin, margin, margin, margin), QPageLayout.Millimeter)


def render_pdf(html_text, paper, path):
    doc, size, margin, page_h = prepare(html_text, paper)
    w = QPdfWriter(str(path))
    _configure(w, size, margin)
    _paint(w, doc, page_h)
    del w  # flush the file to disk


def make_printer(paper, html_text=None):
    doc, size, margin, _ = prepare(html_text or "<p></p>", paper)
    p = QPrinter(QPrinter.HighResolution)
    _configure(p, size, margin)
    return p


def print_html(html_text, paper, parent=None):
    """Standard Windows print dialog, then print. Returns True if printed."""
    printer = make_printer(paper, html_text)
    if QPrintDialog(printer, parent).exec() != QPrintDialog.Accepted:
        return False
    doc, _, _, page_h = prepare(html_text, paper)
    _paint(printer, doc, page_h)
    return True


def preview_html(html_text, paper, parent=None):
    dlg = QPrintPreviewDialog(make_printer(paper, html_text), parent)
    dlg.setWindowTitle("Print preview")

    def draw(printer):
        doc, _, _, page_h = prepare(html_text, paper)
        _paint(printer, doc, page_h)

    dlg.paintRequested.connect(draw)
    dlg.resize(950, 750)
    dlg.exec()


def save_pdf(html_text, paper, parent=None, name="document"):
    start = Path.home() / "Documents"
    start = (start if start.exists() else Path.home()) / f"{name}.pdf"
    path, _ = QFileDialog.getSaveFileName(parent, "Save as PDF", str(start), "PDF files (*.pdf)")
    if not path:
        return None
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    try:
        render_pdf(html_text, paper, path)
    except OSError as e:
        QMessageBox.warning(parent, "Could not save", str(e))
        return None
    QMessageBox.information(parent, "Saved", f"PDF saved to:\n{path}\n\nYou can now send it on WhatsApp or email.")
    return path


class PrintBar(QWidget):
    """Print / Preview / Save PDF buttons. `get_doc()` returns (html, paper, file_name)
    and is called fresh on every click, so it always prints what is on screen."""

    def __init__(self, get_doc, parent=None):
        super().__init__(parent)
        self.get_doc = get_doc
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        for text, fn in [("Print", self.do_print), ("Preview", self.do_preview), ("Save PDF", self.do_pdf)]:
            b = QPushButton(text)
            b.setStyleSheet("QPushButton{background:white;color:#0f172a;border:1px solid #cbd5e1;border-radius:6px;"
                            "padding:8px 16px;}QPushButton:hover{background:#f1f5f9;}")
            b.clicked.connect(fn)
            h.addWidget(b)

    def do_print(self):
        html_text, paper, _ = self.get_doc()
        print_html(html_text, paper, self)

    def do_preview(self):
        html_text, paper, _ = self.get_doc()
        preview_html(html_text, paper, self)

    def do_pdf(self):
        html_text, paper, name = self.get_doc()
        save_pdf(html_text, paper, self, name)

"""Cheque printing: amount in words, bank layouts (templates), and the painter that draws a cheque.

All Indian banks follow the CTS-2010 cheque standard (202 x 92 mm: date boxes top right, payee line, amount in words,
amount box, A/c payee crossing top left), so the starting layouts below share that geometry. Printers and cheque books
differ by a few millimetres, so every layout can be nudged (Cheque templates) and tested on plain paper."""
import copy
import json
from datetime import date, datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPen

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
        "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n):
    return ONES[n] if n < 20 else TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")


def _below_1000(n):
    out = []
    if n >= 100:
        out.append(ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        out.append(_below_100(n))
    return " ".join(out)


def number_in_words(n):
    """Indian system: Thousand, Lakh, Crore."""
    if n == 0:
        return "Zero"
    parts = []
    for unit, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if n >= unit:
            parts.append(_below_100(n // unit) + " " + name if n // unit < 100 else number_in_words(n // unit) + " " + name)
            n %= unit
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount):
    """12345.50 -> 'Rupees Twelve Thousand Three Hundred Forty Five and Paise Fifty Only'"""
    rupees, paise = divmod(int(round(amount * 100)), 100)
    text = "Rupees " + number_in_words(rupees)
    if paise:
        text += " and Paise " + number_in_words(paise)
    return text + " Only"


def indian_number(amount):
    """1234567.5 -> '12,34,567.50'"""
    whole, frac = f"{amount:.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{whole}.{frac}"


# ---------------------------------------------------------------- layouts
def _base():
    f = lambda x, y, w=0, size=11, on=True, **k: {"x": x, "y": y, "w": w, "size": size, "on": on, **k}
    return {"width": 202.0, "height": 92.0, "dx": 0.0, "dy": 0.0, "paper": "cheque", "a4_x": 10.0, "a4_y": 10.0,
            "fields": {
                "date": f(152.0, 11.0, 0, 12, spacing=5.4),
                "payee": f(28.0, 27.0, 140, 12),
                "words1": f(34.0, 37.0, 132, 11),
                "words2": f(20.0, 45.0, 146, 11),
                "amount": f(158.0, 49.0, 38, 14, align="right"),
                "acpayee": f(26.0, 9.0, 0, 9),
                "cheque_no": f(20.0, 80.0, 40, 9, on=False),
            }}


def default_templates():
    out = {}
    for name in ("HDFC Bank", "SBI", "ICICI Bank"):
        out[name] = _base()
    return out


def load_templates(con):
    from . import db
    t = default_templates()
    raw = db.get_settings(con)["cheque_templates"]
    if raw:
        try:
            saved = json.loads(raw)
            for name, tpl in saved.items():
                base = t.get(name) or _base()
                base.update({k: v for k, v in tpl.items() if k != "fields"})
                for fld, vals in tpl.get("fields", {}).items():
                    base["fields"].setdefault(fld, {}).update(vals)
                t[name] = base
        except (ValueError, AttributeError):
            pass
    return t


def save_templates(con, templates):
    from . import db
    db.save_settings(con, {"cheque_templates": json.dumps(templates)})


# ---------------------------------------------------------------- drawing
def _wrap(fm, text, w1, w2):
    """Splits `text` over two lines of the given widths. Returns (line1, line2, fits)."""
    words, l1 = text.split(), ""
    while words and fm.horizontalAdvance((l1 + " " + words[0]).strip()) <= w1:
        l1 = (l1 + " " + words.pop(0)).strip()
    l2 = " ".join(words)
    return l1, l2, fm.horizontalAdvance(l2) <= w2


def draw_cheque(painter, tpl, data, dpi, origin=(0.0, 0.0), guide=False):
    """Draws one cheque. Positions are in millimetres from the cheque's top-left corner; painter units are device pixels."""
    k = dpi / 25.4
    ox, oy = origin[0] + tpl["dx"], origin[1] + tpl["dy"]
    F = tpl["fields"]
    black = QPen(Qt.black)
    painter.setPen(black)

    def font(size, bold=False, fam="Arial"):
        f = QFont(fam)
        f.setPointSizeF(size)
        f.setBold(bold)
        return f

    def text(fld, s, align=Qt.AlignLeft, bold=False, shrink=True):
        c = F[fld]
        if not c.get("on", True) or not s:
            return
        size = c["size"]
        f = font(size, bold)
        painter.setFont(f)
        w = c["w"] or 60
        fm = QFontMetricsF(f, painter.device())
        while shrink and fm.horizontalAdvance(s) > w * k and size > 6:
            size -= 0.5
            f = font(size, bold)
            painter.setFont(f)
            fm = QFontMetricsF(f, painter.device())
        painter.drawText(QRectF((ox + c["x"]) * k, (oy + c["y"]) * k, w * k, 8 * k), int(align | Qt.AlignVCenter), s)

    if guide:                                         # outline of the cheque, for alignment tests on plain paper
        painter.setPen(QPen(Qt.gray, 1, Qt.DashLine))
        painter.drawRect(QRectF(ox * k, oy * k, tpl["width"] * k, tpl["height"] * k))
        painter.setPen(black)

    d = data["date"] if isinstance(data["date"], date) else date.fromisoformat(data["date"])
    c = F["date"]
    if c.get("on", True):
        painter.setFont(font(c["size"]))
        for i, ch in enumerate(d.strftime("%d%m%Y")):
            painter.drawText(QRectF((ox + c["x"] + i * c.get("spacing", 5.4)) * k, (oy + c["y"]) * k, 5.4 * k, 8 * k),
                             int(Qt.AlignCenter), ch)
    text("payee", data["payee"].upper() if data.get("payee") else "")
    c1 = F["words1"]
    painter.setFont(font(c1["size"]))
    fm = QFontMetricsF(font(c1["size"]), painter.device())
    l1, l2, fits = _wrap(fm, amount_in_words(data["amount"]), F["words1"]["w"] * k, F["words2"]["w"] * k)
    if not fits:                                      # very long amount: use a smaller font for both lines
        for fld in ("words1", "words2"):
            F[fld] = dict(F[fld], size=max(F[fld]["size"] - 2, 7))
        fm = QFontMetricsF(font(F["words1"]["size"]), painter.device())
        l1, l2, _ = _wrap(fm, amount_in_words(data["amount"]), F["words1"]["w"] * k, F["words2"]["w"] * k)
    text("words1", l1, shrink=False)
    text("words2", l2, shrink=False)
    text("amount", f"₹ {indian_number(data['amount'])}/-", Qt.AlignRight, bold=True)
    if data.get("cheque_no"):
        text("cheque_no", str(data["cheque_no"]))
    c = F["acpayee"]
    if data.get("ac_payee", True) and c.get("on", True):
        x, y = (ox + c["x"]) * k, (oy + c["y"]) * k
        painter.setPen(QPen(Qt.black, max(1.0, 0.25 * k)))
        painter.drawLine(int(x), int(y + 9 * k), int(x + 11 * k), int(y))
        painter.drawLine(int(x + 4 * k), int(y + 9 * k), int(x + 15 * k), int(y))
        painter.setFont(font(c["size"], True))
        painter.drawText(QRectF(x + 17 * k, y + 1.5 * k, 30 * k, 6 * k), int(Qt.AlignLeft | Qt.AlignVCenter), "A/C PAYEE")
        painter.setPen(black)


def paint(printer, tpl, data, guide=False):
    """Paints the cheque on a QPrinter / QPdfWriter-backed device (page size already set by configure())."""
    p = QPainter(printer)
    try:
        dpi = printer.resolution() if hasattr(printer, "resolution") else p.device().logicalDpiX()
        origin = (tpl["a4_x"], tpl["a4_y"]) if tpl["paper"] == "a4" else (0.0, 0.0)
        draw_cheque(p, copy.deepcopy(tpl), data, dpi, origin, guide)
    finally:
        p.end()


def configure(printer, tpl):
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize
    if tpl["paper"] == "a4":
        printer.setPageSize(QPageSize(QPageSize.A4))
        printer.setPageOrientation(QPageLayout.Portrait)
    else:
        # the size is given exactly as width x height, so the orientation must stay Portrait (Landscape would swap it again)
        printer.setPageSize(QPageSize(QSizeF(tpl["width"], tpl["height"]), QPageSize.Millimeter))
        printer.setPageOrientation(QPageLayout.Portrait)
    printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)


def log_print(con, d):
    con.execute("INSERT INTO cheque_prints(payment_id, party_id, payee, amount, cheque_no, cheque_date, template) VALUES(?,?,?,?,?,?,?)",
                (d.get("payment_id"), d.get("party_id"), d["payee"], d["amount"], d.get("cheque_no"),
                 d["date"].isoformat() if hasattr(d["date"], "isoformat") else d["date"], d.get("template")))
    con.commit()

"""Barcode label printing: Code 128 barcodes drawn directly (no extra library), on A4 label sheets or a label roll."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter

# Code 128 bar patterns for symbol values 0-105 (1 = bar, 0 = space), then the stop pattern with its end bar.
PATTERNS = (
    "11011001100", "11001101100", "11001100110", "10010011000",
    "10010001100", "10001001100", "10011001000", "10011000100",
    "10001100100", "11001001000", "11001000100", "11000100100",
    "10110011100", "10011011100", "10011001110", "10111001100",
    "10011101100", "10011100110", "11001110010", "11001011100",
    "11001001110", "11011100100", "11001110100", "11101101110",
    "11101001100", "11100101100", "11100100110", "11101100100",
    "11100110100", "11100110010", "11011011000", "11011000110",
    "11000110110", "10100011000", "10001011000", "10001000110",
    "10110001000", "10001101000", "10001100010", "11010001000",
    "11000101000", "11000100010", "10110111000", "10110001110",
    "10001101110", "10111011000", "10111000110", "10001110110",
    "11101110110", "11010001110", "11000101110", "11011101000",
    "11011100010", "11011101110", "11101011000", "11101000110",
    "11100010110", "11101101000", "11101100010", "11100011010",
    "11101111010", "11001000010", "11110001010", "10100110000",
    "10100001100", "10010110000", "10010000110", "10000101100",
    "10000100110", "10110010000", "10110000100", "10011010000",
    "10011000010", "10000110100", "10000110010", "11000010010",
    "11001010000", "11110111010", "11000010100", "10001111010",
    "10100111100", "10010111100", "10010011110", "10111100100",
    "10011110100", "10011110010", "11110100100", "11110010100",
    "11110010010", "11011011110", "11011110110", "11110110110",
    "10101111000", "10100011110", "10001011110", "10111101000",
    "10111100010", "11110101000", "11110100010", "10111011110",
    "10111101110", "11101011110", "11110101110", "11010000100",
    "11010010000", "11010011100",
)
STOP = "11000111010" + "11"
START_B = 104

# name -> (paper, label width mm, height mm, columns, rows). A4 sheets are centred on the page; a roll prints one label per page.
LAYOUTS = {
    "A4 sheet - 40 labels (48.5 x 25.4 mm)": ("a4", 48.5, 25.4, 4, 10),
    "A4 sheet - 24 labels (63.5 x 33.9 mm)": ("a4", 63.5, 33.9, 3, 8),
    "A4 sheet - 65 labels (38.1 x 21.2 mm)": ("a4", 38.1, 21.2, 5, 13),
    "Label roll - 50 x 25 mm": ("roll", 50.0, 25.0, 1, 1),
    "Label roll - 38 x 25 mm": ("roll", 38.0, 25.0, 1, 1),
}


def encode(text):
    """Code 128 (set B, printable ASCII) as a string of 1s and 0s. Raises ValueError for characters it cannot hold."""
    if not text:
        raise ValueError("Nothing to encode.")
    vals = []
    for ch in text:
        v = ord(ch) - 32
        if not 0 <= v <= 94:
            raise ValueError(f"The barcode '{text}' has a character that cannot be printed as a barcode.")
        vals.append(v)
    check = (START_B + sum(i * v for i, v in enumerate(vals, 1))) % 103
    return "".join(PATTERNS[v] for v in [START_B] + vals + [check]) + STOP


def products_for_labels(con, search=""):
    like = f"%{search}%"
    return con.execute(
        """SELECT p.id, p.name, p.barcode,
                  COALESCE((SELECT b.mrp FROM batches b WHERE b.product_id = p.id ORDER BY (b.qty > 0) DESC, b.id DESC LIMIT 1), 0) AS mrp,
                  COALESCE((SELECT b.sale_price FROM batches b WHERE b.product_id = p.id ORDER BY (b.qty > 0) DESC, b.id DESC LIMIT 1), 0) AS price,
                  COALESCE((SELECT SUM(b.qty) FROM batches b WHERE b.product_id = p.id), 0) AS stock
           FROM products p WHERE p.active = 1 AND (p.name LIKE ? OR IFNULL(p.barcode,'') LIKE ? OR IFNULL(p.sku,'') LIKE ?)
           ORDER BY p.name""", (like, like, like)).fetchall()


def ensure_barcode(con, product_id):
    """The product's barcode; makes (and saves) one like SD000012 when it has none, so the scanner finds it when billing."""
    r = con.execute("SELECT barcode FROM products WHERE id = ?", (product_id,)).fetchone()
    if r["barcode"]:
        return r["barcode"]
    code = f"SD{product_id:06d}"
    con.execute("UPDATE products SET barcode = ? WHERE id = ?", (code, product_id))
    con.commit()
    return code


def configure(printer, layout):
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize
    paper, w, h, *_ = LAYOUTS[layout]
    if paper == "a4":
        printer.setPageSize(QPageSize(QPageSize.A4))
    else:
        printer.setPageSize(QPageSize(QSizeF(w, h), QPageSize.Millimeter))
    printer.setPageOrientation(QPageLayout.Portrait)
    printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)


def draw_label(p, rect, item, opts, px):
    """One label inside `rect` (pixels). item: name, barcode, mrp, price. px = pixels per mm."""
    pad = 1.2 * px
    inner = rect.adjusted(pad, pad, -pad, -pad)
    y = inner.top()
    p.setPen(QColor("black"))
    small = QFont("Arial")
    small.setPixelSize(max(int(2.6 * px), 6))
    if opts.get("shop"):
        p.setFont(small)
        p.drawText(QRectF(inner.left(), y, inner.width(), 3.2 * px), Qt.AlignCenter, opts["shop"])
        y += 3.2 * px
    name_font = QFont("Arial")
    name_font.setPixelSize(max(int(3.2 * px), 7))
    name_font.setBold(True)
    p.setFont(name_font)
    name_h = 7.0 * px if inner.height() > 18 * px else 3.8 * px
    fm = QFontMetricsF(name_font)
    p.drawText(QRectF(inner.left(), y, inner.width(), name_h), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
               fm.elidedText(item["name"], Qt.ElideRight, inner.width() * (2 if name_h > 5 * px else 1)))
    y += name_h
    price_h = 3.8 * px if opts.get("price", "mrp") != "none" else 0
    text_h = 3.0 * px
    bars_h = max(inner.bottom() - y - price_h - text_h, 4 * px)
    bits = encode(item["barcode"])
    quiet = 2 * px
    mod = (inner.width() - 2 * quiet) / len(bits)
    x = inner.left() + quiet
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("black"))
    i = 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            p.drawRect(QRectF(x + i * mod, y, (j - i) * mod, bars_h))
            i = j
        else:
            i += 1
    p.setPen(QColor("black"))
    p.setFont(small)
    p.drawText(QRectF(inner.left(), y + bars_h, inner.width(), text_h), Qt.AlignCenter, item["barcode"])
    kind = opts.get("price", "mrp")
    if kind != "none":
        val = item["mrp"] if kind == "mrp" else item["price"]
        price_font = QFont("Arial")
        price_font.setPixelSize(max(int(3.4 * px), 7))
        price_font.setBold(True)
        p.setFont(price_font)
        p.drawText(QRectF(inner.left(), y + bars_h + text_h, inner.width(), price_h), Qt.AlignCenter,
                   f"{'MRP' if kind == 'mrp' else 'Price'} Rs {val:,.2f}".replace(".00", ""))


def expand(items):
    """[(item, copies)] -> flat list of labels."""
    return [it for it, n in items for _ in range(int(n))]


def paint(device, layout, items, opts):
    """items: [(item dict, copies)]. opts: shop, price (mrp|price|none), skip (labels to leave blank at the start), dx, dy (mm)."""
    from PySide6.QtGui import QPageLayout
    paper, w, h, cols, rows = LAYOUTS[layout]
    labels = [None] * (int(opts.get("skip", 0)) if paper == "a4" else 0) + expand(items)
    p = QPainter(device)
    try:
        dpi = device.resolution() if hasattr(device, "resolution") else p.device().logicalDpiX()
        px = dpi / 25.4
        per = cols * rows
        x0 = (210 - cols * w) / 2 + opts.get("dx", 0.0) if paper == "a4" else 0.0
        y0 = (297 - rows * h) / 2 + opts.get("dy", 0.0) if paper == "a4" else 0.0
        for n, item in enumerate(labels):
            if n and n % per == 0:
                device.newPage()
            if item is None:
                continue
            k = n % per
            r, c = divmod(k, cols)
            draw_label(p, QRectF((x0 + c * w) * px, (y0 + r * h) * px, w * px, h * px), item, opts, px)
    finally:
        p.end()

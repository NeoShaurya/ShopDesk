"""Report data. Every report returns the same dict shape, so ONE table view and ONE
print routine work for all of them:
    title, subtitle, headers, aligns ('l'/'r' per column), rows, footer, ids
"""
import math
from datetime import datetime

from . import currency as currency_mod
from . import db


def fmt(v):
    return f"{v:,.2f}"


def _when(s):
    return datetime.fromisoformat(s).strftime("%d %b %Y, %I:%M %p")


def _day(s):
    return datetime.fromisoformat(s).strftime("%d %b %Y")


def sales_report(con, d1, d2):
    rs = con.execute(
        """SELECT s.id, s.invoice_no, s.sale_date, COALESCE(c.name,'-') AS cust,
                  s.total, s.paid, s.payment_mode, s.frozen
           FROM sales s LEFT JOIN parties c ON c.id = s.customer_id
           WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ? ORDER BY s.sale_date DESC""", (d1, d2)).fetchall()
    rows = [[("🔒 " if r["frozen"] else "") + r["invoice_no"], _when(r["sale_date"]), r["cust"], fmt(r["total"]), fmt(r["paid"]),
             fmt(r["total"] - r["paid"]) if r["total"] - r["paid"] > 0.005 else "-", r["payment_mode"].title()]
            for r in rs]
    tot, paid = sum(r["total"] for r in rs), sum(r["paid"] for r in rs)
    return {"title": "Sales Report", "subtitle": f"{_day(d1)} to {_day(d2)}  |  {len(rs)} bills",
            "headers": ["Bill no.", "Date", "Customer", "Total", "Paid", "Due", "Mode"],
            "aligns": "lllrrrl", "rows": rows, "ids": [r["id"] for r in rs],
            "footer": [["", "", "Total", fmt(tot), fmt(paid), fmt(tot - paid), ""]]}


def stock_report(con):
    rs = con.execute(
        """SELECT p.name, p.category, p.reorder_level, COALESCE(SUM((b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0))),0) AS stock,
                  COALESCE(SUM((b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0))*b.cost_price),0) AS cost_val, COALESCE(SUM((b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0))*b.sale_price),0) AS sale_val
           FROM products p LEFT JOIN batches b ON b.product_id = p.id
           WHERE p.active = 1 GROUP BY p.id ORDER BY p.name""").fetchall()
    rows = [[r["name"], r["category"] or "", r["stock"], fmt(r["cost_val"]), fmt(r["sale_val"]),
             "Out of stock" if r["stock"] == 0 else "Low" if r["stock"] <= r["reorder_level"] else "OK"]
            for r in rs]
    return {"title": "Stock Report", "subtitle": f"As on {datetime.now():%d %b %Y}  |  {len(rs)} products",
            "headers": ["Product", "Category", "In stock", "Value at cost", "Value at sale price", "Status"],
            "aligns": "llrrrl", "rows": rows, "ids": [],
            "footer": [["Total", "", sum(r["stock"] for r in rs), fmt(sum(r["cost_val"] for r in rs)),
                        fmt(sum(r["sale_val"] for r in rs)), ""]]}


def expiry_report(con, days=30):
    rs = con.execute(
        """SELECT p.name, b.batch_no, b.expiry_date, (b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0)) AS qty, (b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0))*b.cost_price AS val,
                  CAST(julianday(b.expiry_date) - julianday(date('now','localtime')) AS INTEGER) AS dl
           FROM batches b JOIN products p ON p.id = b.product_id
           WHERE (b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0)) > 0 AND b.expiry_date IS NOT NULL AND b.expiry_date <= date('now','localtime', ?)
           ORDER BY b.expiry_date""", (f"+{days} days",)).fetchall()
    rows = [[r["name"], r["batch_no"], _day(r["expiry_date"]),
             f"EXPIRED {-r['dl']}d ago" if r["dl"] < 0 else "Today" if r["dl"] == 0 else f"{r['dl']} days",
             r["qty"], fmt(r["val"])] for r in rs]
    return {"title": "Expiry Report", "subtitle": f"Expired or expiring within {days} days",
            "headers": ["Product", "Batch", "Expiry date", "Time left", "Qty", "Value at risk"],
            "aligns": "lllrrr", "rows": rows, "ids": [],
            "footer": [["Total", "", "", "", sum(r["qty"] for r in rs), fmt(sum(r["val"] for r in rs))]]}


def cancelled_bills(con, d1, d2):
    rs = con.execute("""SELECT s.invoice_no, s.sale_date, s.cancelled_at, s.cancel_reason, s.total, COALESCE(c.name,'Walk-in Customer') AS cust
                        FROM sales s LEFT JOIN parties c ON c.id = s.customer_id
                        WHERE s.cancelled = 1 AND date(s.sale_date) BETWEEN ? AND ? ORDER BY s.id DESC""", (d1, d2)).fetchall()
    rows = [[r["invoice_no"], _when(r["sale_date"]), r["cust"], fmt(r["total"]), _when(r["cancelled_at"]) if r["cancelled_at"] else "",
             r["cancel_reason"] or ""] for r in rs]
    return {"title": "Cancelled Bills", "subtitle": f"{_day(d1)} to {_day(d2)}  |  {len(rs)} cancelled",
            "headers": ["Bill", "Date", "Customer", "Amount", "Cancelled on", "Reason"], "aligns": "lllrll", "rows": rows, "ids": [],
            "footer": [["TOTAL", "", "", fmt(sum(r["total"] for r in rs)), "", ""]]}


def profit_report(con, d1, d2):
    rs = con.execute(
        """SELECT p.name, SUM(si.qty) AS qty, SUM(si.line_total) AS rev, SUM(si.qty*si.cost_price) AS cost
           FROM sale_items si JOIN sales s ON s.id = si.sale_id JOIN products p ON p.id = si.product_id
           WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ? GROUP BY p.id ORDER BY (rev - cost) DESC""", (d1, d2)).fetchall()
    disc = con.execute("SELECT COALESCE(SUM(discount),0) FROM sales WHERE cancelled = 0 AND date(sale_date) BETWEEN ? AND ?",
                       (d1, d2)).fetchone()[0]
    rev, cost = sum(r["rev"] for r in rs), sum(r["cost"] for r in rs)
    rows = [[r["name"], r["qty"], fmt(r["rev"]), fmt(r["cost"]), fmt(r["rev"] - r["cost"]),
             f"{(r['rev'] - r['cost']) / r['rev'] * 100:.0f}%" if r["rev"] else "-"] for r in rs]
    return {"title": "Profit & Loss", "subtitle": f"{_day(d1)} to {_day(d2)}  (sales excluding GST)",
            "headers": ["Product", "Qty sold", "Sales", "Cost", "Profit", "Margin"],
            "aligns": "lrrrrr", "rows": rows, "ids": [],
            "footer": [["Gross profit", sum(r["qty"] for r in rs), fmt(rev), fmt(cost), fmt(rev - cost),
                        f"{(rev - cost) / rev * 100:.0f}%" if rev else "-"],
                       ["Less: bill discounts", "", "", "", fmt(-disc), ""],
                       ["NET PROFIT", "", "", "", fmt(rev - cost - disc), ""]]}


def currency_report(con, d1, d2):
    """Bills, purchases and payments made in a currency other than the base currency, with both amounts."""
    base = currency_mod.base_code(con)
    ev = []
    for r in con.execute("""SELECT s.sale_date AS d, 'Sale' AS t, s.invoice_no AS no, COALESCE(c.name,'Walk-in Customer') AS party, s.currency, s.orig_total AS orig,
                                   s.exchange_rate AS rate, s.total AS amt FROM sales s LEFT JOIN parties c ON c.id = s.customer_id
                            WHERE s.cancelled = 0 AND s.currency IS NOT NULL AND s.currency != ? AND date(s.sale_date) BETWEEN ? AND ?""", (base, d1, d2)):
        ev.append(r)
    for r in con.execute("""SELECT u.purchase_date AS d, 'Purchase' AS t, COALESCE(NULLIF(u.bill_no,''), '#' || u.id) AS no, COALESCE(p.name,'-') AS party,
                                   u.currency, u.orig_total AS orig, u.exchange_rate AS rate, u.total AS amt FROM purchases u LEFT JOIN parties p ON p.id = u.supplier_id
                            WHERE u.currency IS NOT NULL AND u.currency != ? AND date(u.purchase_date) BETWEEN ? AND ?""", (base, d1, d2)):
        ev.append(r)
    for r in con.execute("""SELECT a.paid_on AS d, CASE WHEN p.type = 'customer' THEN 'Receipt' ELSE 'Payment' END AS t, 'PAY-' || a.id AS no, p.name AS party,
                                   a.currency, a.orig_amount AS orig, a.exchange_rate AS rate, a.amount AS amt FROM payments a JOIN parties p ON p.id = a.party_id
                            WHERE a.currency IS NOT NULL AND a.currency != ? AND date(a.paid_on) BETWEEN ? AND ?""", (base, d1, d2)):
        ev.append(r)
    ev.sort(key=lambda r: r["d"])
    rows = [[_when(r["d"]) if len(r["d"]) > 10 else _day(r["d"]), r["t"], r["no"], r["party"], r["currency"], fmt(r["orig"] or 0), f"{r['rate']:g}",
             fmt(r["amt"])] for r in ev]
    foot, per = [], {}
    for r in ev:
        key = (r["currency"], r["t"])
        a = per.setdefault(key, [0.0, 0.0])
        a[0] += r["orig"] or 0
        a[1] += r["amt"]
    for (cur, t), (o, b) in sorted(per.items()):
        foot.append([f"{t} total", "", "", "", cur, fmt(o), "", fmt(b)])
    return {"title": "Foreign Currency Transactions", "subtitle": f"{_day(d1)} to {_day(d2)}  |  amounts in {base} use the rate of that day",
            "headers": ["Date", "Type", "No.", "Party", "Currency", "Amount (original)", "Rate", f"Amount ({base})"],
            "aligns": "llllrrrr", "rows": rows, "ids": [], "footer": foot or [["No foreign currency entries", "", "", "", "", "", "", ""]]}


def ledger_report(con, party_id, d1=None, d2=None):
    p = db.get_party(con, party_id)
    cust = p["type"] == "customer"
    rows = db.party_ledger(con, party_id, d1, d2)
    out = [[_day(r["date"]) if r["date"] else "", ("🔒 " if r.get("frozen") else "") + r["particulars"], fmt(r["debit"]) if r["debit"] else "",
            fmt(r["credit"]) if r["credit"] else "", fmt(r["balance"])] for r in rows]
    bal = rows[-1]["balance"] if rows else 0.0
    bits = [x for x in (p["gstin"] and f"GSTIN {p['gstin']}", p["phone"]) if x]
    base = currency_mod.base_code(con)
    if p["currency"] and p["currency"] != base:
        cur, rate = currency_mod.resolve(con, p["currency"])
        bits.append(f"Closing balance {currency_mod.show(cur, currency_mod.from_base(bal, rate))} at today's rate of {rate:g}")
    return {"title": f"{'Customer' if cust else 'Supplier'} Ledger - {p['name']}",
            "subtitle": "  |  ".join(bits + [f"{_day(d1)} to {_day(d2)}" if d1 and d2 else f"As on {datetime.now():%d %b %Y}"]),
            "headers": ["Date", "Particulars", "Debit", "Credit", "Balance"], "aligns": "llrrr", "rows": out, "ids": [],
            "footer": [["", "Total", fmt(sum(r["debit"] for r in rows)), fmt(sum(r["credit"] for r in rows)), ""],
                       ["", "Closing balance - " + ("customer owes you" if cust else "you owe supplier"), "", "", fmt(bal)]]}


def reorder_report(con, cover_days=30, lead_days=7):
    """Purchase order suggestion. Order enough to cover `cover_days` of recent sales on top of the reorder
    level. A product is listed when stock is at/below its reorder level or would run out within `lead_days`."""
    rs = con.execute(
        """SELECT p.id, p.name, p.reorder_level,
                  COALESCE((SELECT SUM((b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0))) FROM batches b WHERE b.product_id = p.id AND (b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0)) > 0
                            AND (b.expiry_date IS NULL OR b.expiry_date >= date('now','localtime'))), 0) AS stock,
                  COALESCE((SELECT SUM(si.qty) FROM sale_items si JOIN sales s ON s.id = si.sale_id
                            WHERE s.cancelled = 0 AND si.product_id = p.id AND date(s.sale_date) >= date('now','localtime','-29 days')), 0) AS sold30,
                  (SELECT b.cost_price FROM batches b WHERE b.product_id = p.id ORDER BY b.id DESC LIMIT 1) AS cost,
                  (SELECT pa.name FROM batches b JOIN parties pa ON pa.id = b.supplier_id
                   WHERE b.product_id = p.id ORDER BY b.id DESC LIMIT 1) AS supplier
           FROM products p WHERE p.active = 1""").fetchall()
    items = []
    for r in rs:
        per_day = r["sold30"] / 30.0
        days_left = r["stock"] / per_day if per_day else None
        if not (r["stock"] <= r["reorder_level"] or (days_left is not None and days_left < lead_days)):
            continue
        qty = math.ceil(r["reorder_level"] + per_day * cover_days - r["stock"])
        if qty <= 0:
            continue
        items.append((r["supplier"] or "(no supplier yet)", r["name"], r["stock"], r["reorder_level"], r["sold30"],
                      days_left, qty, r["cost"] or 0.0))
    items.sort(key=lambda x: (x[0].lower(), x[1].lower()))
    rows = [[sup, name, stock, reorder, sold, "-" if dl is None else f"{dl:.0f}", qty, fmt(cost), fmt(qty * cost)]
            for sup, name, stock, reorder, sold, dl, qty, cost in items]
    return {"title": "Purchase Order Suggestion",
            "subtitle": f"Covers {cover_days} days of sales + reorder level  |  {len(rows)} products  |  "
                        "costs are the last purchase price (excl. GST)",
            "headers": ["Supplier", "Product", "In stock", "Reorder at", "Sold (30d)", "Days left", "Order qty",
                        "Last cost", "Est. cost"],
            "aligns": "llrrrrrrr", "rows": rows, "ids": [],
            "footer": [["Total", "", "", "", "", "", sum(i[6] for i in items), "", fmt(sum(i[6] * i[7] for i in items))]]}


def purchases_report(con, d1, d2):
    rs = con.execute(
        """SELECT pu.purchase_date, COALESCE(NULLIF(pu.bill_no,''), '#' || pu.id) AS inv, COALESCE(s.name,'-') AS supplier,
                  COALESCE(s.gstin,'') AS gstin, pu.taxable_value, pu.gst_amount, pu.total, pu.advance_amount,
                  pu.balance_due, COALESCE(pu.payment_method,'') AS mode
           FROM purchases pu LEFT JOIN parties s ON s.id = pu.supplier_id
           WHERE date(pu.purchase_date) BETWEEN ? AND ? ORDER BY pu.purchase_date DESC, pu.id DESC""", (d1, d2)).fetchall()
    rows = [[_day(r["purchase_date"]), r["inv"], r["supplier"], r["gstin"], fmt(r["taxable_value"]), fmt(r["gst_amount"]),
             fmt(r["total"]), fmt(r["advance_amount"]), fmt(r["balance_due"]) if r["balance_due"] > 0.005 else "-",
             r["mode"].title()] for r in rs]
    s = lambda k: sum(r[k] for r in rs)
    return {"title": "Purchase Report", "subtitle": f"{_day(d1)} to {_day(d2)}  |  {len(rs)} purchases",
            "headers": ["Date", "Invoice no.", "Supplier", "GSTIN", "Taxable", "GST", "Total", "Paid", "Due", "Mode"],
            "aligns": "llllrrrrrl", "rows": rows, "ids": [],
            "footer": [["", "", "Total", "", fmt(s("taxable_value")), fmt(s("gst_amount")), fmt(s("total")),
                        fmt(s("advance_amount")), fmt(s("balance_due")), ""]]}


def parties_report(con, ptype):
    rs = db.list_parties(con, ptype)
    rows = [[r["name"], r["phone"] or "", r["gstin"] or "", r["address"] or "", fmt(r["balance"])] for r in rs]
    cust = ptype == "customer"
    return {"title": "Customers" if cust else "Suppliers",
            "subtitle": f"{len(rs)} {'customers' if cust else 'suppliers'}  |  balance = " +
                        ("amount they owe you" if cust else "amount you owe them"),
            "headers": ["Name", "Phone", "GSTIN", "Address", "Balance"], "aligns": "llllr", "rows": rows, "ids": [],
            "footer": [["Total outstanding", "", "", "", fmt(sum(max(r["balance"], 0) for r in rs))]]}


# ---------------------------------------------------------------- cheques (PDC)
def _pdc_rows(rs):
    return [[_day(r["cheque_date"]), r["party"], "Received" if r["kind"] == "received" else "Issued", r["cheque_no"],
             r["bank"], fmt(r["amount"]), r["status"].title()] for r in rs]


def _pdc_report(title, subtitle, rs):
    rec = sum(r["amount"] for r in rs if r["kind"] == "received")
    iss = sum(r["amount"] for r in rs if r["kind"] == "issued")
    return {"title": title, "subtitle": subtitle, "headers": ["Cheque date", "Party", "Type", "Cheque no.", "Bank", "Amount", "Status"],
            "aligns": "lllllrl", "rows": _pdc_rows(rs), "ids": [],
            "footer": [["", f"{len(rs)} cheque(s)", "Received", "", "", fmt(rec), ""], ["", "", "Issued", "", "", fmt(iss), ""]]}


def pdc_register(con, d1, d2):
    from . import pdc
    return _pdc_report("PDC Register", f"Cheque dates {_day(d1)} to {_day(d2)}", pdc.listing(con, d1=d1, d2=d2))


def pdc_pending(con):
    from . import pdc
    return _pdc_report("Pending PDCs", f"Not yet cleared, as on {datetime.now():%d %b %Y}", pdc.listing(con, status="open"))


def pdc_bounced(con):
    from . import pdc
    return _pdc_report("Bounced Cheques", f"As on {datetime.now():%d %b %Y}", pdc.listing(con, status="bounced"))

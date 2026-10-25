"""Accounting reports: day book, cash / bank book, outstanding, stock valuation, profit & loss, balance sheet,
GST summary, item-wise sales and purchases. Same dict shape as reports.py (title, subtitle, headers, aligns, rows, footer, ids).

How the numbers are worked out
- Money in / out comes from what was actually paid: the part paid at billing, payments from / to parties, refunds, expenses.
  Cash goes to the cash book; UPI, card, cheque and bank transfer go to the bank book.
- Profit uses the cost saved on each sold item, so it stays right when buying prices change later.
- Stock on an earlier date is worked back from today's stock using the stock movement log, valued at today's cost."""
from datetime import date, datetime, timedelta

from . import db, expenses, gstr
from .gst import valid_gstin
from .reports import _day, fmt


def _ymd(s):
    return (s or "")[:10]


def _book_of(method):
    return "cash" if (method or "cash").lower() in ("cash", "credit", "mixed", "") else "bank"


def _party_name(name, default="Walk-in Customer"):
    return name or default


# ---------------------------------------------------------------- money in / out
def money_events(con):
    """Every movement of cash / bank money: [{when, book, kind, ref, party, inn, out, note}] (unsorted)."""
    ev = []

    def add(when, method, kind, ref, party, inn=0.0, out=0.0, note=""):
        ev.append({"when": when, "book": _book_of(method), "kind": kind, "ref": ref, "party": party, "inn": inn, "out": out,
                   "note": note or ""})

    for r in con.execute("""SELECT s.invoice_no, s.sale_date, s.advance_amount, s.advance_method, c.name FROM sales s
                            LEFT JOIN parties c ON c.id = s.customer_id WHERE s.cancelled = 0 AND s.advance_amount > 0"""):
        add(r["sale_date"], r["advance_method"], "Sale", r["invoice_no"], _party_name(r["name"]), inn=r["advance_amount"],
            note="Received with bill")
    for r in con.execute("""SELECT pu.id, pu.bill_no, pu.purchase_date, pu.advance_amount, pu.advance_method, s.name FROM purchases pu
                            LEFT JOIN parties s ON s.id = pu.supplier_id WHERE pu.advance_amount > 0"""):
        add(r["purchase_date"], r["advance_method"], "Purchase", r["bill_no"] or f"#{r['id']}", _party_name(r["name"], "Cash purchase"),
            out=r["advance_amount"], note="Paid with purchase")
    for r in con.execute("""SELECT pa.id, pa.paid_on, pa.amount, pa.mode, pa.note, p.name, p.type FROM payments pa
                            JOIN parties p ON p.id = pa.party_id"""):
        if r["type"] == "customer":
            add(r["paid_on"], r["mode"], "Receipt", f"RCT-{r['id']:05d}", r["name"], inn=r["amount"], note=r["note"])
        else:
            add(r["paid_on"], r["mode"], "Payment", f"PMT-{r['id']:05d}", r["name"], out=r["amount"], note=r["note"])
    for r in con.execute("""SELECT r.note_no, r.kind, r.created_at, r.total, r.refund_method, p.name FROM returns r
                            LEFT JOIN parties p ON p.id = r.party_id WHERE r.refund_method != 'adjust' AND r.total > 0"""):
        if r["kind"] == "customer":
            add(r["created_at"], r["refund_method"], "Refund", r["note_no"], _party_name(r["name"]), out=r["total"], note="Refund to customer")
        else:
            add(r["created_at"], r["refund_method"], "Refund", r["note_no"], _party_name(r["name"], "Supplier"), inn=r["total"],
                note="Refund from supplier")
    for r in con.execute("SELECT * FROM expenses"):
        add(r["spent_on"], r["mode"], "Expense", r["category"], r["note"] or "", out=r["amount"])
    return ev


def _opening(con, book):
    return float(db.get_settings(con).get("opening_cash" if book == "cash" else "opening_bank") or 0)


def balance_of(con, book, upto=None):
    """Cash or bank balance at the end of `upto` (ISO date; default: now)."""
    bal = _opening(con, book)
    for e in money_events(con):
        if e["book"] == book and (upto is None or _ymd(e["when"]) <= upto):
            bal += e["inn"] - e["out"]
    return round(bal, 2)


def _book(con, d1, d2, book):
    title = "Cash Book" if book == "cash" else "Bank Book"
    ev = sorted((e for e in money_events(con) if e["book"] == book), key=lambda e: e["when"])
    bal = _opening(con, book) + sum(e["inn"] - e["out"] for e in ev if _ymd(e["when"]) < d1)
    rows = [[_day(d1), "Opening balance", "", "", "", fmt(bal)]]
    tin = tout = 0.0
    for e in ev:
        if not (d1 <= _ymd(e["when"]) <= d2):
            continue
        bal += e["inn"] - e["out"]
        tin, tout = tin + e["inn"], tout + e["out"]
        text = e["party"] if e["kind"] != "Expense" else f"{e['ref']}" + (f" - {e['party']}" if e["party"] else "")
        rows.append([_day(e["when"]), f"{e['kind']}: {text}" + (f" ({e['note']})" if e["note"] and e["kind"] != "Expense" else ""),
                     e["ref"] if e["kind"] != "Expense" else "", fmt(e["inn"]) if e["inn"] else "", fmt(e["out"]) if e["out"] else "",
                     fmt(bal)])
    return {"title": title, "subtitle": f"{_day(d1)} to {_day(d2)}" + ("  |  cash received and paid" if book == "cash" else
            "  |  UPI, card, cheque and bank transfer"), "headers": ["Date", "Particulars", "Ref", "Receipts", "Payments", "Balance"],
            "aligns": "lllrrr", "rows": rows, "ids": [],
            "footer": [["", "Total for the period", "", fmt(tin), fmt(tout), ""], ["", "CLOSING BALANCE", "", "", "", fmt(bal)]]}


def cash_book(con, d1, d2):
    return _book(con, d1, d2, "cash")


def bank_book(con, d1, d2):
    return _book(con, d1, d2, "bank")


# ---------------------------------------------------------------- day book
def day_book(con, d1, d2):
    rows = []                                       # (when, type, no, party, amount, settled)
    for r in con.execute("""SELECT s.sale_date, s.invoice_no, s.total, s.advance_amount, COALESCE(c.name,'Walk-in Customer') AS party
                            FROM sales s LEFT JOIN parties c ON c.id = s.customer_id WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ?""", (d1, d2)):
        rows.append((r["sale_date"], "Sales", r["invoice_no"], r["party"], r["total"], r["advance_amount"]))
    for r in con.execute("""SELECT pu.purchase_date, COALESCE(NULLIF(pu.bill_no,''),'#'||pu.id) AS no, pu.total, pu.advance_amount,
                                   COALESCE(s.name,'Cash purchase') AS party FROM purchases pu LEFT JOIN parties s ON s.id = pu.supplier_id
                            WHERE date(pu.purchase_date) BETWEEN ? AND ?""", (d1, d2)):
        rows.append((r["purchase_date"], "Purchase", r["no"], r["party"], r["total"], r["advance_amount"]))
    for r in con.execute("""SELECT r.created_at, r.note_no, r.kind, r.total, r.refund_method, COALESCE(p.name,'Walk-in Customer') AS party
                            FROM returns r LEFT JOIN parties p ON p.id = r.party_id WHERE date(r.created_at) BETWEEN ? AND ?""", (d1, d2)):
        rows.append((r["created_at"], "Credit note" if r["kind"] == "customer" else "Debit note", r["note_no"], r["party"], r["total"],
                     r["total"] if r["refund_method"] != "adjust" else 0.0))
    for r in con.execute("""SELECT pa.id, pa.paid_on, pa.amount, pa.mode, p.name, p.type FROM payments pa JOIN parties p ON p.id = pa.party_id
                            WHERE date(pa.paid_on) BETWEEN ? AND ?""", (d1, d2)):
        rows.append((r["paid_on"], "Receipt" if r["type"] == "customer" else "Payment",
                     f"{'RCT' if r['type'] == 'customer' else 'PMT'}-{r['id']:05d}", r["name"], r["amount"], r["amount"]))
    for r in con.execute("SELECT * FROM expenses WHERE date(spent_on) BETWEEN ? AND ?", (d1, d2)):
        rows.append((r["spent_on"], "Expense", r["category"], r["note"] or "", r["amount"], r["amount"]))
    rows.sort(key=lambda x: x[0])
    out = [[datetime.fromisoformat(w if len(w) > 10 else w + " 00:00:00").strftime("%d %b %Y, %I:%M %p"), t, no, party, fmt(a),
            fmt(s) if s else "-"] for w, t, no, party, a, s in rows]
    totals = {}
    for _, t, _, _, a, s in rows:
        tot = totals.setdefault(t, [0, 0.0, 0.0])
        tot[0] += 1
        tot[1] += a
        tot[2] += s
    footer = [["", t, f"{n} entr{'y' if n == 1 else 'ies'}", "", fmt(a), fmt(s)] for t, (n, a, s) in sorted(totals.items())]
    return {"title": "Day Book", "subtitle": f"{_day(d1)}" + (f" to {_day(d2)}" if d2 != d1 else "") + f"  |  {len(rows)} entries",
            "headers": ["Date & time", "Type", "No.", "Party / details", "Amount", "Paid / received now"], "aligns": "llllrr",
            "rows": out, "ids": [], "footer": footer}


# ---------------------------------------------------------------- outstanding
def _aging(invoices, balance, today):
    """Spreads `balance` over the newest invoices first (older ones were paid first). Returns [0-30, 31-60, 61-90, 90+]."""
    b, left = [0.0] * 4, balance
    for when, amount in sorted(invoices, key=lambda x: x[0], reverse=True):
        if left <= 0.005:
            break
        take = min(left, amount)
        days = (today - date.fromisoformat(_ymd(when))).days
        b[0 if days <= 30 else 1 if days <= 60 else 2 if days <= 90 else 3] += take
        left -= take
    b[3] += max(left, 0.0)                          # balance older than any bill on record
    return b


def outstanding(con, ptype):
    cust = ptype == "customer"
    today = date.today()
    rows, tot = [], [0.0] * 5
    for p in con.execute("SELECT * FROM parties WHERE type = ? AND name != 'Walk-in Customer' AND balance > 0.005 ORDER BY name", (ptype,)):
        inv = ([(r["sale_date"], r["total"]) for r in con.execute("SELECT sale_date, total FROM sales WHERE cancelled = 0 AND customer_id = ?", (p["id"],))]
               if cust else
               [(r["purchase_date"], r["total"]) for r in con.execute("SELECT purchase_date, total FROM purchases WHERE supplier_id = ?", (p["id"],))])
        b = _aging(inv, p["balance"], today)
        rows.append([p["name"], p["phone"] or "", fmt(p["balance"])] + [fmt(x) if x else "-" for x in b])
        for i, v in enumerate([p["balance"]] + b):
            tot[i] += v
    return {"title": "Outstanding Receivables" if cust else "Outstanding Payables",
            "subtitle": f"As on {today:%d %b %Y}  |  {len(rows)} {'customers owe you' if cust else 'suppliers are owed'}"
                        "  |  age counted from the newest bills (older bills are assumed paid first)",
            "headers": ["Party", "Phone", "Outstanding", "0-30 days", "31-60 days", "61-90 days", "Over 90 days"],
            "aligns": "llrrrrr", "rows": rows, "ids": [], "footer": [["TOTAL", "", *[fmt(x) for x in tot]]]}


# ---------------------------------------------------------------- stock
def batch_qty_at(con, as_on=None):
    """Rows per batch with the quantity held (all locations) at the end of `as_on`."""
    rows = []
    for r in con.execute(
            f"""SELECT b.id, b.batch_no, b.expiry_date, b.cost_price, b.mrp, b.sale_price, p.id AS pid, p.name, p.category,
                       {db.TOTAL_QTY} AS qty_now,
                       COALESCE((SELECT SUM(m.change_qty) FROM stock_movements m WHERE m.batch_id = b.id AND m.reason != 'transfer'
                                 AND date(m.created_at) > ?), 0) AS later
                FROM batches b JOIN products p ON p.id = b.product_id WHERE p.active = 1 ORDER BY p.name, b.expiry_date""",
            (as_on or "9999-12-31",)):
        q = r["qty_now"] - (r["later"] if as_on else 0)
        if q > 0:
            rows.append({**dict(r), "qty": q})
    return rows


def stock_value_at(con, as_on=None):
    return round(sum(r["qty"] * r["cost_price"] for r in batch_qty_at(con, as_on)), 2)


def stock_valuation(con, as_on=None):
    as_on = as_on or date.today().isoformat()
    agg = {}
    for r in batch_qty_at(con, as_on):
        a = agg.setdefault(r["pid"], {"name": r["name"], "cat": r["category"] or "", "qty": 0, "cost": 0.0, "sale": 0.0})
        a["qty"] += r["qty"]
        a["cost"] += r["qty"] * r["cost_price"]
        a["sale"] += r["qty"] * r["sale_price"]
    items = sorted(agg.values(), key=lambda a: a["name"])
    rows = [[a["name"], a["cat"], a["qty"], fmt(a["cost"] / a["qty"]), fmt(a["cost"]), fmt(a["sale"]), fmt(a["sale"] - a["cost"])]
            for a in items]
    c, s = sum(a["cost"] for a in items), sum(a["sale"] for a in items)
    return {"title": "Stock Valuation", "subtitle": f"As on {_day(as_on)}  |  all locations  |  at average cost (without GST)",
            "headers": ["Product", "Category", "Qty", "Avg cost", "Value at cost", "Value at selling price", "Expected profit"],
            "aligns": "llrrrrr", "rows": rows, "ids": [],
            "footer": [["TOTAL", "", sum(a["qty"] for a in items), "", fmt(c), fmt(s), fmt(s - c)]]}


def batch_valuation(con, as_on=None):
    as_on = as_on or date.today().isoformat()
    rows, c, s, q = [], 0.0, 0.0, 0
    for r in batch_qty_at(con, as_on):
        cost, sale = r["qty"] * r["cost_price"], r["qty"] * r["sale_price"]
        rows.append([r["name"], r["batch_no"], _day(r["expiry_date"]) if r["expiry_date"] else "-", r["qty"], fmt(r["cost_price"]),
                     fmt(cost), fmt(sale)])
        c, s, q = c + cost, s + sale, q + r["qty"]
    return {"title": "Batch-wise Stock Valuation", "subtitle": f"As on {_day(as_on)}  |  each batch valued at its own cost (without GST)",
            "headers": ["Product", "Batch", "Expiry", "Qty", "Cost / unit", "Value at cost", "Value at selling price"],
            "aligns": "lllrrrr", "rows": rows, "ids": [], "footer": [["TOTAL", "", "", q, "", fmt(c), fmt(s)]]}


# ---------------------------------------------------------------- profit & loss
def pnl_figures(con, d1, d2):
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0] or 0.0
    sales = q("""SELECT SUM(subtotal - CASE WHEN subtotal + tax > 0 THEN discount * subtotal / (subtotal + tax) ELSE 0 END)
                 FROM sales WHERE cancelled = 0 AND date(sale_date) BETWEEN ? AND ?""", d1, d2)
    returns = q("SELECT SUM(taxable_value) FROM returns WHERE kind='customer' AND date(created_at) BETWEEN ? AND ?", d1, d2)
    cost = q("""SELECT SUM(si.qty * si.cost_price) FROM sale_items si JOIN sales s ON s.id = si.sale_id
                WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ?""", d1, d2)
    cost_back = q("""SELECT SUM(ri.qty * si.cost_price) FROM return_items ri JOIN returns r ON r.id = ri.return_id
                     JOIN sale_items si ON si.id = ri.ref_item_id
                     WHERE r.kind='customer' AND r.restocked = 1 AND date(r.created_at) BETWEEN ? AND ?""", d1, d2)
    loss = -q("""SELECT SUM(m.change_qty * b.cost_price) FROM stock_movements m JOIN batches b ON b.id = m.batch_id
                 WHERE m.reason IN ('expired','adjustment') AND IFNULL(m.note,'') NOT LIKE 'Debit note%'
                   AND date(m.created_at) BETWEEN ? AND ?""", d1, d2)
    exp = [(r["category"], r["t"]) for r in con.execute(
        "SELECT category, SUM(amount) AS t FROM expenses WHERE category != ? AND date(spent_on) BETWEEN ? AND ? GROUP BY category ORDER BY 2 DESC",
        (expenses.GST_PAID, d1, d2))]
    net_sales = sales - returns
    cogs = cost - cost_back
    gross = net_sales - cogs
    return {"sales": sales, "returns": returns, "net_sales": net_sales, "cogs": cogs, "gross": gross, "loss": loss,
            "expenses": exp, "exp_total": sum(t for _, t in exp), "net": gross - loss - sum(t for _, t in exp)}


def profit_loss(con, d1, d2):
    f = pnl_figures(con, d1, d2)
    r = lambda label, v="": [label, fmt(0.0 if v != "" and abs(v) < 0.005 else v) if v != "" else ""]
    rows = [r("INCOME"), r("   Sales (without GST, after discounts)", f["sales"]), r("   Less: sales returns", -f["returns"]),
            r("NET SALES", f["net_sales"]), r(""), r("COST OF GOODS SOLD"),
            r("   Cost of items sold (less returns taken back)", f["cogs"]), r("GROSS PROFIT", f["gross"]), r(""),
            r("OTHER LOSSES"), r("   Expired, damaged and adjusted stock (net)", f["loss"]), r(""), r("EXPENSES")]
    rows += [r(f"   {c}", t) for c, t in f["expenses"]] or [r("   (none recorded)")]
    rows += [r("TOTAL EXPENSES", f["exp_total"])]
    return {"title": "Profit & Loss Statement", "subtitle": f"{_day(d1)} to {_day(d2)}", "headers": ["Particulars", "Amount (₹)"],
            "aligns": "lr", "rows": rows, "ids": [],
            "footer": [["NET PROFIT / (LOSS)", fmt(f["net"])]]}


# ---------------------------------------------------------------- balance sheet
def _party_balance_at(con, pid, as_on):
    rows = db.party_ledger(con, pid, None, as_on)
    return rows[-1]["balance"] if rows else 0.0


def gst_position(con, as_on):
    """(output tax, input tax credit, paid to government) up to and including `as_on`."""
    out = con.execute("""SELECT SUM(tax * (1 - CASE WHEN subtotal + tax > 0 THEN discount / (subtotal + tax) ELSE 0 END))
                         FROM sales WHERE cancelled = 0 AND date(sale_date) <= ?""", (as_on,)).fetchone()[0] or 0.0
    out -= con.execute("SELECT SUM(gst_amount) FROM returns WHERE kind='customer' AND date(created_at) <= ?", (as_on,)).fetchone()[0] or 0.0
    registered = lambda alias: f"({alias}.gstin IS NOT NULL AND length(replace({alias}.gstin,' ','')) = 15)"
    inn = con.execute(f"""SELECT SUM(pu.gst_amount) FROM purchases pu JOIN parties s ON s.id = pu.supplier_id
                          WHERE {registered('s')} AND date(pu.purchase_date) <= ?""", (as_on,)).fetchone()[0] or 0.0
    inn -= con.execute(f"""SELECT SUM(r.gst_amount) FROM returns r JOIN parties s ON s.id = r.party_id
                           WHERE r.kind='supplier' AND {registered('s')} AND date(r.created_at) <= ?""", (as_on,)).fetchone()[0] or 0.0
    paid = con.execute("SELECT SUM(amount) FROM expenses WHERE category = ? AND date(spent_on) <= ?",
                       (expenses.GST_PAID, as_on)).fetchone()[0] or 0.0
    return round(out, 2), round(inn, 2), round(paid, 2)


def balance_sheet(con, as_on=None):
    as_on = as_on or date.today().isoformat()
    cash, bank = balance_of(con, "cash", as_on), balance_of(con, "bank", as_on)
    stock = stock_value_at(con, as_on)
    rec = adv_cust = pay = adv_sup = 0.0
    for p in con.execute("SELECT id, type FROM parties WHERE name != 'Walk-in Customer'").fetchall():
        b = _party_balance_at(con, p["id"], as_on)
        if p["type"] == "customer":
            rec, adv_cust = rec + max(b, 0), adv_cust + max(-b, 0)
        else:
            pay, adv_sup = pay + max(b, 0), adv_sup + max(-b, 0)
    out, inn, paid = gst_position(con, as_on)
    gst_net = round(out - inn - paid, 2)
    assets = [("Cash in hand", cash), ("Bank balance", bank), ("Stock (at cost)", stock), ("Customers owe you (receivables)", rec),
              ("Advances paid to suppliers", adv_sup), ("GST credit with government", -gst_net if gst_net < 0 else 0.0)]
    liabs = [("You owe suppliers (payables)", pay), ("Advances received from customers", adv_cust),
             ("GST payable to government", gst_net if gst_net > 0 else 0.0)]
    assets = [(n, round(v, 2)) for n, v in assets if abs(v) > 0.004 or n in ("Cash in hand", "Bank balance", "Stock (at cost)")]
    liabs = [(n, round(v, 2)) for n, v in liabs if abs(v) > 0.004 or n.startswith("You owe")]
    ta, tl = sum(v for _, v in assets), sum(v for _, v in liabs)
    equity = round(ta - tl, 2)
    first = con.execute("SELECT MIN(d) FROM (SELECT MIN(date(sale_date)) d FROM sales WHERE cancelled = 0 UNION ALL SELECT MIN(date(purchase_date)) FROM purchases "
                        "UNION ALL SELECT MIN(date(spent_on)) FROM expenses)").fetchone()[0] or as_on
    profit = pnl_figures(con, first, as_on)["net"]
    rows = [[n, "", ""] for n in ("ASSETS",)] + [[f"   {n}", fmt(v), ""] for n, v in assets]
    rows += [["TOTAL ASSETS", "", fmt(ta)], ["", "", ""], ["LIABILITIES AND CAPITAL", "", ""]]
    rows += [[f"   {n}", fmt(v), ""] for n, v in liabs]
    rows += [["TOTAL LIABILITIES", "", fmt(tl)], ["", "", ""], ["OWNER'S CAPITAL (assets less liabilities)", "", fmt(equity)],
             ["   of which: profit earned since you started", fmt(profit), ""],
             ["   of which: capital put in / earlier balances", fmt(equity - profit), ""]]
    return {"title": "Balance Sheet", "subtitle": f"As on {_day(as_on)}  |  stock valued at today's cost; owner's capital is the balancing figure",
            "headers": ["Particulars", "Amount (₹)", "Total (₹)"], "aligns": "lrr", "rows": rows, "ids": [],
            "footer": [["TOTAL LIABILITIES + CAPITAL", "", fmt(tl + equity)]]}


# ---------------------------------------------------------------- GST summary
def gst_summary(con, d1, d2):
    data = gstr.collect_range(con, d1, d2)
    rate_set = sorted({r["rate"] for k in ("sales", "credits", "purchases", "debits") for r in data[k]})
    rows, tot = [], [0.0] * 8
    for rate in rate_set:
        def side(main, back, only_reg=False):
            m = [r for r in data[main] if r["rate"] == rate and (r["registered"] or not only_reg)]
            b = [r for r in data[back] if r["rate"] == rate and (r["registered"] or not only_reg)]
            taxable = sum(r["taxable"] for r in m) - sum(r["taxable"] for r in b)
            heads = [sum(r["split"][i] for r in m) - sum(r["split"][i] for r in b) for i in range(3)]
            return [taxable] + heads
        o, i = side("sales", "credits"), side("purchases", "debits", only_reg=True)
        vals = o + i
        rows.append([f"{rate:g}%", *[fmt(v) for v in vals]])
        tot = [a + b for a, b in zip(tot, vals)]
    out_tax, in_tax = sum(tot[1:4]), sum(tot[5:8])
    return {"title": "Sales Tax (GST) Summary", "subtitle": f"{_day(d1)} to {_day(d2)}  |  output tax on sales less credit notes, input tax on "
            "purchases from suppliers with a GSTIN less debit notes",
            "headers": ["Rate", "Sales taxable", "Output IGST", "Output CGST", "Output SGST", "Purchases taxable", "Input IGST",
                        "Input CGST", "Input SGST"], "aligns": "lrrrrrrrr", "rows": rows, "ids": [],
            "footer": [["TOTAL", *[fmt(v) for v in tot]], ["Output tax", "", "", "", fmt(out_tax), "", "", "", ""],
                       ["Input tax credit", "", "", "", "", "", "", "", fmt(in_tax)],
                       ["NET GST PAYABLE / (CREDIT)", "", "", "", fmt(out_tax - in_tax), "", "", "", ""]]}


# ---------------------------------------------------------------- item-wise
def item_sales(con, d1, d2):
    agg = {}
    for r in con.execute("""SELECT p.id, p.name, p.hsn_code, SUM(si.qty) AS qty, SUM(si.line_total) AS rev, SUM(si.qty * si.cost_price) AS cost
                            FROM sale_items si JOIN sales s ON s.id = si.sale_id JOIN products p ON p.id = si.product_id
                            WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ? GROUP BY p.id""", (d1, d2)):
        agg[r["id"]] = {"name": r["name"], "hsn": r["hsn_code"] or "", "qty": r["qty"], "rev": r["rev"], "cost": r["cost"], "rq": 0, "rrev": 0.0}
    for r in con.execute("""SELECT ri.product_id AS id, SUM(ri.qty) AS q, SUM(ri.taxable_value) AS t, SUM(ri.qty * si.cost_price) AS c, r.restocked
                            FROM return_items ri JOIN returns r ON r.id = ri.return_id JOIN sale_items si ON si.id = ri.ref_item_id
                            WHERE r.kind = 'customer' AND date(r.created_at) BETWEEN ? AND ? GROUP BY ri.product_id, r.restocked""", (d1, d2)):
        a = agg.get(r["id"])
        if a:
            a["rq"] += r["q"]
            a["rrev"] += r["t"]
            if r["restocked"]:
                a["cost"] -= r["c"]
    items = sorted(agg.values(), key=lambda a: -(a["rev"] - a["rrev"]))
    rows = [[a["name"], a["hsn"], a["qty"], a["rq"], a["qty"] - a["rq"], fmt(a["rev"] - a["rrev"]), fmt(a["cost"]),
             fmt(a["rev"] - a["rrev"] - a["cost"])] for a in items]
    s = lambda k: sum(a[k] for a in items)
    return {"title": "Item-wise Sales", "subtitle": f"{_day(d1)} to {_day(d2)}  |  sales without GST, after returns",
            "headers": ["Product", "HSN", "Qty sold", "Returned", "Net qty", "Net sales", "Cost", "Profit"], "aligns": "llrrrrrr",
            "rows": rows, "ids": [], "footer": [["TOTAL", "", s("qty"), s("rq"), s("qty") - s("rq"), fmt(s("rev") - s("rrev")),
                                                  fmt(s("cost")), fmt(s("rev") - s("rrev") - s("cost"))]]}


def item_purchases(con, d1, d2):
    agg = {}
    for r in con.execute("""SELECT p.id, p.name, p.hsn_code, SUM(pi.qty) AS qty, SUM(pi.taxable_value) AS t, SUM(pi.gst_amount) AS g
                            FROM purchase_items pi JOIN purchases pu ON pu.id = pi.purchase_id JOIN products p ON p.id = pi.product_id
                            WHERE date(pu.purchase_date) BETWEEN ? AND ? GROUP BY p.id""", (d1, d2)):
        agg[r["id"]] = {"name": r["name"], "hsn": r["hsn_code"] or "", "qty": r["qty"], "t": r["t"], "g": r["g"], "rq": 0, "rt": 0.0, "rg": 0.0}
    for r in con.execute("""SELECT ri.product_id AS id, SUM(ri.qty) AS q, SUM(ri.taxable_value) AS t, SUM(ri.gst_amount) AS g
                            FROM return_items ri JOIN returns r ON r.id = ri.return_id
                            WHERE r.kind = 'supplier' AND date(r.created_at) BETWEEN ? AND ? GROUP BY ri.product_id""", (d1, d2)):
        a = agg.get(r["id"])
        if a:
            a["rq"], a["rt"], a["rg"] = r["q"], r["t"], r["g"]
    items = sorted(agg.values(), key=lambda a: -(a["t"] - a["rt"]))
    rows = [[a["name"], a["hsn"], a["qty"] - a["rq"], fmt((a["t"] - a["rt"]) / (a["qty"] - a["rq"])) if a["qty"] - a["rq"] else "-",
             fmt(a["t"] - a["rt"]), fmt(a["g"] - a["rg"]), fmt(a["t"] - a["rt"] + a["g"] - a["rg"])] for a in items]
    s = lambda k: sum(a[k] for a in items)
    return {"title": "Item-wise Purchases", "subtitle": f"{_day(d1)} to {_day(d2)}  |  after returns to suppliers",
            "headers": ["Product", "HSN", "Net qty", "Avg cost", "Taxable", "GST", "Total"], "aligns": "llrrrrr", "rows": rows, "ids": [],
            "footer": [["TOTAL", "", s("qty") - s("rq"), "", fmt(s("t") - s("rt")), fmt(s("g") - s("rg")),
                        fmt(s("t") - s("rt") + s("g") - s("rg"))]]}

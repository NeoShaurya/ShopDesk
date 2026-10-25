"""SQLite access + every query the dashboard needs. One connection, plain SQL."""
import os
import sqlite3
import sys
from pathlib import Path

from . import currency as currency_mod



def data_dir():
    """Where the shop's data lives. Installed copy: the user's own folder (the program folder is read-only).
    Running from the source folder: ./data. SHOPDESK_DATA overrides both."""
    if os.environ.get("SHOPDESK_DATA"):
        return Path(os.environ["SHOPDESK_DATA"])
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        return Path(base) / "ShopDesk" / "data"
    return Path(__file__).resolve().parent.parent / "data"


DB_PATH = data_dir() / "shop.db"
SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path=DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrate(con)
    return con


def _add_column(con, table, column, decl):
    """Adds the column if it is missing. Returns True when it had to be added."""
    if column in [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]:
        return False
    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def _migrate(con):
    """Upgrade databases created by earlier versions of the app (safe to run every start)."""
    _add_column(con, "sales", "tax_inclusive", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "products", "barcode", "TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL")

    # payment fields: sales
    if _add_column(con, "sales", "payment_method", "TEXT"):
        _add_column(con, "sales", "advance_amount", "REAL NOT NULL DEFAULT 0")
        _add_column(con, "sales", "advance_method", "TEXT")
        _add_column(con, "sales", "balance_due", "REAL NOT NULL DEFAULT 0")
        con.execute("""UPDATE sales SET payment_method = payment_mode, advance_amount = paid,
                       advance_method = CASE WHEN paid > 0 AND payment_mode IN ('cash','upi','card') THEN payment_mode END,
                       balance_due = MAX(total - paid, 0)""")
    # payment fields + tax split: purchases
    if _add_column(con, "purchases", "payment_method", "TEXT"):
        _add_column(con, "purchases", "advance_amount", "REAL NOT NULL DEFAULT 0")
        _add_column(con, "purchases", "advance_method", "TEXT")
        _add_column(con, "purchases", "balance_due", "REAL NOT NULL DEFAULT 0")
        _add_column(con, "purchases", "taxable_value", "REAL NOT NULL DEFAULT 0")
        _add_column(con, "purchases", "gst_amount", "REAL NOT NULL DEFAULT 0")
        con.execute("""UPDATE purchases SET payment_method = 'cash', advance_amount = paid, taxable_value = total,
                       advance_method = CASE WHEN paid > 0 THEN 'cash' END, balance_due = MAX(total - paid, 0)""")

    _add_column(con, "parties", "price_list_id", "INTEGER")
    _add_column(con, "parties", "trade_discount", "REAL NOT NULL DEFAULT 0")
    _add_column(con, "parties", "state_code", "TEXT")
    if not con.execute("SELECT 1 FROM godowns").fetchone():
        con.execute("INSERT INTO godowns(name, is_main) VALUES('Main shop', 1)")
    if not con.execute("SELECT 1 FROM price_lists").fetchone():       # first list = the default for everyone
        for name, basis in (("Retail", "sale_price"), ("Wholesale", "sale_price"), ("MRP", "mrp")):
            con.execute("INSERT INTO price_lists(name, basis) VALUES(?,?)", (name, basis))
    if not con.execute("SELECT 1 FROM parties WHERE type='customer' AND name='Walk-in Customer'").fetchone():
        con.execute("INSERT INTO parties(type,name) VALUES('customer','Walk-in Customer')")
    con.commit()
    from . import audit, currency, extras, freeze, fy
    extras.install(con)
    currency.install(con)
    fy.install(con)
    freeze.install(con)
    audit.install(con)


# Quantity of a batch in ALL locations (main shop + godowns). `b` must be the batches alias.
TOTAL_QTY = "(b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0))"


# ---------- Dashboard queries ----------

def expiring(con, days: int):
    """Batches with stock (anywhere) that expire within `days` (includes already expired)."""
    row = con.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM({TOTAL_QTY} * b.cost_price), 0) AS value
            FROM batches b
            WHERE {TOTAL_QTY} > 0 AND b.expiry_date IS NOT NULL
              AND b.expiry_date <= date('now','localtime', ?)""",
        (f"+{days} days",),
    ).fetchone()
    return row["n"], row["value"]


def expiring_list(con, days: int = 30, limit: int = 8):
    return con.execute(
        f"""SELECT p.name, b.batch_no, b.expiry_date, {TOTAL_QTY} AS qty,
                   CAST(julianday(b.expiry_date) - julianday(date('now','localtime')) AS INTEGER) AS days_left
            FROM batches b JOIN products p ON p.id = b.product_id
            WHERE {TOTAL_QTY} > 0 AND b.expiry_date IS NOT NULL
              AND b.expiry_date <= date('now','localtime', ?)
            ORDER BY b.expiry_date LIMIT ?""",
        (f"+{days} days", limit),
    ).fetchall()


def export_today(con, limit: int = 8):
    return con.execute(
        """SELECT p.name, b.batch_no, b.qty, b.sale_price
           FROM batches b JOIN products p ON p.id = b.product_id
           WHERE b.export_ready = 1 AND b.qty > 0
           ORDER BY p.name LIMIT ?""",
        (limit,),
    ).fetchall()


def low_stock(con, limit: int = 8):
    """Products whose TOTAL stock (all locations) is at or below the reorder level: time to order."""
    return con.execute(
        f"""SELECT p.name, p.reorder_level, COALESCE(SUM({TOTAL_QTY}), 0) AS stock
            FROM products p LEFT JOIN batches b ON b.product_id = p.id
            WHERE p.active = 1
            GROUP BY p.id HAVING stock <= p.reorder_level
            ORDER BY (stock * 1.0 / MAX(p.reorder_level, 1)) LIMIT ?""",
        (limit,),
    ).fetchall()


def fast_movers(con, limit: int = 5):
    return con.execute(
        """SELECT p.name, SUM(si.qty) AS sold, SUM(si.line_total) AS revenue
           FROM sale_items si
           JOIN sales s ON s.id = si.sale_id
           JOIN products p ON p.id = si.product_id
           WHERE s.cancelled = 0 AND date(s.sale_date) >= date('now','localtime','-6 days')
           GROUP BY p.id ORDER BY sold DESC LIMIT ?""",
        (limit,),
    ).fetchall()


def slow_movers(con, limit: int = 6, quiet_days: int = 7):
    """In stock, but nothing sold in the last `quiet_days`. Nearest expiry first
    so the owner knows what to discount."""
    return con.execute(
        """SELECT p.name, SUM(b.qty) AS stock, MIN(b.expiry_date) AS next_expiry,
                  SUM(b.qty * b.cost_price) AS value
           FROM products p JOIN batches b ON b.product_id = p.id
           WHERE b.qty > 0 AND p.active = 1
             AND p.id NOT IN (
                 SELECT si.product_id FROM sale_items si
                 JOIN sales s ON s.id = si.sale_id
                 WHERE s.cancelled = 0 AND date(s.sale_date) >= date('now','localtime', ?))
           GROUP BY p.id
           ORDER BY (next_expiry IS NULL), next_expiry LIMIT ?""",
        (f"-{quiet_days} days", limit),
    ).fetchall()


def today_summary(con):
    def day(offset):
        return con.execute(
            """SELECT COUNT(*) AS bills, COALESCE(SUM(total),0) AS sales
               FROM sales WHERE cancelled = 0 AND date(sale_date) = date('now','localtime', ?)""",
            (offset,),
        ).fetchone()

    t, y = day("+0 days"), day("-1 day")
    profit = con.execute(
        """SELECT COALESCE(SUM(si.line_total - si.qty * si.cost_price), 0)
           FROM sale_items si JOIN sales s ON s.id = si.sale_id
           WHERE s.cancelled = 0 AND date(s.sale_date) = date('now','localtime')"""
    ).fetchone()[0]
    change = ((t["sales"] - y["sales"]) / y["sales"] * 100) if y["sales"] else None
    return {"sales": t["sales"], "bills": t["bills"], "profit": profit, "vs_yesterday": change}


# ---------- Inventory ----------

def list_inventory(con, search: str = "", flt: str = "all"):
    """One row per batch (products with no batch still appear, with NULL batch)."""
    sql = """SELECT p.id AS product_id, p.name, p.category, p.reorder_level,
                    b.id AS batch_id, b.batch_no, b.expiry_date, b.qty,
                    b.cost_price, b.mrp, b.sale_price, b.export_ready,
                    (SELECT COALESCE(SUM(b3.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b3.id), 0)), 0)
                     FROM batches b3 WHERE b3.product_id = p.id) AS total_stock,
                    COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs WHERE gs.batch_id = b.id), 0) AS in_godowns,
                    CAST(julianday(b.expiry_date) - julianday(date('now','localtime')) AS INTEGER) AS days_left
             FROM products p LEFT JOIN batches b ON b.product_id = p.id
             WHERE p.active = 1 AND (p.name LIKE ? OR p.sku LIKE ? OR IFNULL(b.batch_no,'') LIKE ?
                   OR IFNULL(p.barcode,'') LIKE ?)"""
    like = f"%{search}%"
    rows = con.execute(sql + " ORDER BY p.name, b.expiry_date", (like, like, like, like)).fetchall()
    if flt == "low":
        rows = [r for r in rows if r["total_stock"] <= r["reorder_level"]]
    elif flt == "expiring":
        rows = [r for r in rows if r["days_left"] is not None and r["days_left"] <= 30 and r["qty"] > 0]
    elif flt == "export":
        rows = [r for r in rows if r["export_ready"]]
    return rows


def list_products(con):
    return con.execute("SELECT * FROM products WHERE active = 1 ORDER BY name").fetchall()


def get_product(con, pid):
    return con.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()


def get_batch(con, bid):
    return con.execute("SELECT * FROM batches WHERE id = ?", (bid,)).fetchone()


def save_product(con, d: dict, pid=None):
    from . import gst
    problem = gst.check_product(d, gst.rates(con))
    if problem:
        raise ValueError(problem)
    cols = ("name", "sku", "category", "hsn_code", "barcode", "unit", "gst_percent", "reorder_level")
    vals = [d.get(c) for c in cols]
    if pid:
        con.execute(f"UPDATE products SET {', '.join(c + '=?' for c in cols)} WHERE id=?", vals + [pid])
    else:
        cur = con.execute(f"INSERT INTO products({', '.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        pid = cur.lastrowid
    con.commit()
    return pid


def bulk_change_gst(con, old_rate, new_rate, category=None, keep_final_price=False):
    """Moves every active product on `old_rate` to `new_rate` (optionally only one category).
    keep_final_price: for shops that price EXCLUDING GST, scale the selling prices (batches and fixed price-list
    prices) so the customer still pays the same total. Returns the number of products changed.
    Each product change is in the audit log automatically; one summary line is added too."""
    from . import audit
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM products WHERE active = 1 AND gst_percent = ? AND (? IS NULL OR category = ?)",
        (old_rate, category, category))]
    if not ids:
        return 0
    try:
        marks = ",".join("?" * len(ids))
        if keep_final_price:
            f = (1 + old_rate / 100) / (1 + new_rate / 100)
            con.execute(f"UPDATE batches SET sale_price = ROUND(sale_price * ?, 2) WHERE product_id IN ({marks})", [f] + ids)
            con.execute(f"UPDATE price_list_items SET price = ROUND(price * ?, 2) WHERE product_id IN ({marks})", [f] + ids)
        con.execute(f"UPDATE products SET gst_percent = ? WHERE id IN ({marks})", [new_rate] + ids)
        audit.note(con, "products", "bulk gst change",
                   f"{old_rate:g}% to {new_rate:g}% on {len(ids)} product(s)" + (f" in {category}" if category else "")
                   + (", selling prices adjusted to keep the final price" if keep_final_price else ""))
        con.commit()
        return len(ids)
    except Exception:
        con.rollback()
        raise


def save_batch(con, d: dict, bid=None):
    """Insert/update a batch; any quantity change is written to stock_movements."""
    cols = ("product_id", "batch_no", "expiry_date", "qty", "cost_price", "mrp", "sale_price", "export_ready")
    vals = [d.get(c) for c in cols]
    if bid:
        old = con.execute("SELECT qty FROM batches WHERE id=?", (bid,)).fetchone()["qty"]
        con.execute(f"UPDATE batches SET {', '.join(c + '=?' for c in cols)} WHERE id=?", vals + [bid])
        if d["qty"] != old:
            con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason) VALUES(?,?,'adjustment')",
                        (bid, d["qty"] - old))
    else:
        cur = con.execute(f"INSERT INTO batches({', '.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
        bid = cur.lastrowid
        if d["qty"]:
            con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason) VALUES(?,?,'purchase')",
                        (bid, d["qty"]))
    con.commit()
    return bid


def set_export_ready(con, bid, flag: bool):
    con.execute("UPDATE batches SET export_ready=? WHERE id=?", (1 if flag else 0, bid))
    con.commit()


def delete_batch(con, bid):
    """Raises sqlite3.IntegrityError if the batch has sales history."""
    con.execute("DELETE FROM stock_movements WHERE batch_id=?", (bid,))
    con.execute("DELETE FROM batches WHERE id=?", (bid,))
    con.commit()


def archive_product(con, pid):
    """Hide a product without losing its sales history."""
    con.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
    con.commit()


# ---------- Settings ----------

DEFAULT_SETTINGS = {
    "shop_name": "My Shop", "shop_address": "", "shop_phone": "", "shop_gstin": "",
    "thanks_msg": "Thank you for shopping with us! Visit again.",
    "paper": "a4",            # a4 | t80 | t58
    "tax_inclusive": "0",     # "1" = selling prices already include GST
    "country_code": "91",     # used to build WhatsApp numbers
    "dark_mode": "0",
    "base_currency": "INR",
    "credit_limit_action": "warn",    # warn | block when a customer goes past the credit limit
    "opening_cash": "0", "opening_bank": "0",      # balances before the first entry (cash book, bank book, balance sheet)
    "cheque_template": "HDFC Bank",   # default bank layout for printing cheques
    "cheque_templates": "",            # your adjusted layouts (JSON); empty = standard
    "gst_rates": "0, 5, 12, 18, 28",   # GST rates allowed on products (Settings > GST rates)
    # expiry alerts
    "alert_enabled": "0", "alert_days": "30", "alert_email_to": "", "alert_wa_number": "",
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_pass": "", "smtp_from": "",
    "alert_sms_url": "", "alert_sms_numbers": "",
    "alert_last_sent": "", "alert_last_try": "", "alert_last_result": "",
}


def get_settings(con) -> dict:
    d = dict(DEFAULT_SETTINGS)
    d.update({r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")})
    return d


def save_settings(con, d: dict):
    con.executemany("INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    [(k, str(v)) for k, v in d.items()])
    con.commit()


# ---------- Billing ----------

_SELLABLE = "b.qty > 0 AND (b.expiry_date IS NULL OR b.expiry_date >= date('now','localtime'))"


def list_customers(con):
    return con.execute("SELECT * FROM parties WHERE type='customer' ORDER BY (name!='Walk-in Customer'), name").fetchall()


def add_customer(con, name: str, phone: str = "") -> int:
    cur = con.execute("INSERT INTO parties(type,name,phone) VALUES('customer',?,?)", (name, phone))
    con.commit()
    return cur.lastrowid


def _first_batch(col):
    return (f"(SELECT b.{col} FROM batches b WHERE b.product_id = p.id AND {_SELLABLE} "
            "ORDER BY (b.expiry_date IS NULL), b.expiry_date, b.id LIMIT 1)")


def sellable_products(con):
    """Products that can be sold today (expired batches never count), with the price,
    batch number and batch size of the batch that will be used first (nearest expiry)."""
    return con.execute(
        f"""SELECT p.id, p.name, p.gst_percent, p.hsn_code, SUM(b.qty) AS stock,
                   {_first_batch('sale_price')} AS price, {_first_batch('batch_no')} AS batch_no,
                   {_first_batch('qty')} AS batch_qty, {_first_batch('mrp')} AS mrp
            FROM products p JOIN batches b ON b.product_id = p.id
            WHERE p.active = 1 AND {_SELLABLE}
            GROUP BY p.id ORDER BY p.name"""
    ).fetchall()


def product_id_by_barcode(con, code):
    r = con.execute("SELECT id FROM products WHERE barcode = ? AND active = 1", (code.strip(),)).fetchone()
    return r["id"] if r else None


def _fefo_batches(con, product_id):
    return con.execute(
        f"""SELECT * FROM batches b WHERE b.product_id = ? AND {_SELLABLE}
            ORDER BY (b.expiry_date IS NULL), b.expiry_date, b.id""", (product_id,)).fetchall()


def compute_totals(lines, discount=0.0, inclusive=False):
    """lines: iterable of dicts with qty, price, gst (percent) and optional disc (line discount %).
    Prices are ex-GST unless `inclusive`. subtotal is always the ex-GST amount after line discounts;
    `discount` is the extra invoice-level discount in rupees."""
    sub = tax = 0.0
    for l in lines:
        amt = l["qty"] * l["price"] * (1 - l.get("disc", 0) / 100)
        g = l["gst"] / 100
        if inclusive:
            net = amt / (1 + g)
            sub += net
            tax += amt - net
        else:
            sub += amt
            tax += amt * g
    total = max(round(sub + tax - discount, 2), 0.0)
    return {"subtotal": round(sub, 2), "tax": round(tax, 2), "discount": round(discount, 2), "total": total}


def below_cost(con, lines, inclusive=False):
    """Names of products on this bill whose selling price (ex-GST, after line discount) is under the
    cost of the batch that will be sold first."""
    out = []
    for l in lines:
        row = con.execute(
            f"""SELECT p.name, {_first_batch('cost_price')} AS cost FROM products p WHERE p.id = ?""",
            (l["product_id"],)).fetchone()
        if not row or row["cost"] is None:
            continue
        net = l["price"] * (1 - l.get("disc", 0) / 100) / ((1 + l["gst"] / 100) if inclusive else 1)
        if net < row["cost"] - 0.005 and row["name"] not in out:
            out.append(row["name"])
    return out


def create_sale(con, customer_id, lines, discount=0.0, paid=None, mode="cash", inclusive=False, advance_method=None, series_id=None, currency=None):
    """Save a bill. lines: [{product_id, qty, price, gst, disc=0 (line discount %), free=0 (scheme units)}].
    Stock (paid + free units) is taken from the nearest-expiry batches first. Everything happens in one
    transaction; raises ValueError (nothing saved) if any product lacks stock.
    mode = payment method (cash|upi|card|credit|mixed); paid = advance / amount paid now;
    the rest is balance_due and is added to the customer's account."""
    from . import fy
    fy.guard(con)
    cur, rate = currency_mod.resolve(con, currency)
    try:
        allocs = []                                   # (line, batch, paid units, free units)
        for l in lines:
            paid_left, free_left = l["qty"], l.get("free", 0)
            need = paid_left + free_left
            for b in _fefo_batches(con, l["product_id"]):
                take = min(b["qty"], need)
                pt = min(take, paid_left)
                allocs.append((l, b, pt, take - pt))
                paid_left -= pt
                free_left -= take - pt
                need -= take
                if need == 0:
                    break
            if need:
                name = con.execute("SELECT name FROM products WHERE id=?", (l["product_id"],)).fetchone()["name"]
                raise ValueError(f"Not enough stock for {name}")

        t = compute_totals(lines, discount, inclusive)
        paid = t["total"] if paid is None else min(paid, t["total"])
        due = round(t["total"] - paid, 2)
        if advance_method is None and paid > 0 and mode in ("cash", "upi", "card"):
            advance_method = mode
        if paid <= 0:
            advance_method = None
        from . import extras
        if due > 0:
            over = extras.credit_check(con, customer_id, due)
            if over and extras.credit_action(con) == "block":
                raise ValueError(extras.credit_message(over) + "\n\nCollect more now, or raise the customer's credit limit.")
        invoice_no = extras.take_number(con, series_id)
        sale_id = con.execute(
            """INSERT INTO sales(invoice_no,customer_id,subtotal,discount,tax,total,paid,payment_mode,tax_inclusive,
                                 payment_method,advance_amount,advance_method,balance_due,currency,exchange_rate,orig_total)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (invoice_no, customer_id, t["subtotal"], t["discount"], t["tax"], t["total"], paid, mode,
             1 if inclusive else 0, mode, paid, advance_method, due, cur, rate, currency_mod.from_base(t["total"], rate))).lastrowid
        for l, b, pt, ft in allocs:
            g, d = l["gst"] / 100, l.get("disc", 0) / 100
            if pt:
                gross = pt * l["price"]
                disc_amt = round(gross * d, 2)
                after = gross - disc_amt
                net = after / (1 + g) if inclusive else after
                con.execute(
                    """INSERT INTO sale_items(sale_id,product_id,batch_id,qty,unit_price,cost_price,discount,gst_percent,line_total)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (sale_id, l["product_id"], b["id"], pt, l["price"], b["cost_price"], disc_amt, l["gst"], round(net, 2)))
            if ft:                                    # free goods: no charge, but they leave stock and cost money
                con.execute(
                    """INSERT INTO sale_items(sale_id,product_id,batch_id,qty,unit_price,cost_price,discount,gst_percent,line_total)
                       VALUES(?,?,?,?,0,?,0,?,0)""",
                    (sale_id, l["product_id"], b["id"], ft, b["cost_price"], l["gst"]))
            con.execute("UPDATE batches SET qty = qty - ? WHERE id = ?", (pt + ft, b["id"]))
            con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,ref_id,note) VALUES(?,?,'sale',?,?)",
                        (b["id"], -(pt + ft), sale_id, f"{ft} free (scheme)" if ft else None))
        if customer_id and due > 0:
            con.execute("UPDATE parties SET balance = balance + ? WHERE id = ?", (due, customer_id))
        con.commit()
        return sale_id
    except Exception:
        con.rollback()
        raise


def get_sale(con, sale_id):
    return con.execute(
        """SELECT s.*, COALESCE(c.name, 'Walk-in Customer') AS customer_name, c.gstin AS customer_gstin
           FROM sales s LEFT JOIN parties c ON c.id = s.customer_id WHERE s.id = ?""", (sale_id,)).fetchone()


def get_sale_items(con, sale_id):
    """Lines as printed on the bill (a product split across batches is merged; free units are their own line)."""
    return con.execute(
        """SELECT CASE WHEN si.unit_price = 0 THEN p.name || ' (free)' ELSE p.name END AS name, p.hsn_code,
                  SUM(si.qty) AS qty, si.unit_price, si.gst_percent, SUM(si.discount) AS discount
           FROM sale_items si JOIN products p ON p.id = si.product_id
           WHERE si.sale_id = ? GROUP BY si.product_id, si.unit_price, si.gst_percent
           ORDER BY MIN(si.id)""", (sale_id,)).fetchall()


# ---------- Customers & suppliers ----------

def list_parties(con, ptype, search=""):
    like = f"%{search}%"
    return con.execute(
        """SELECT * FROM parties WHERE type = ? AND (name LIKE ? OR IFNULL(phone,'') LIKE ? OR IFNULL(gstin,'') LIKE ?)
           ORDER BY (name != 'Walk-in Customer'), name""", (ptype, like, like, like)).fetchall()


def get_party(con, pid):
    return con.execute("SELECT * FROM parties WHERE id = ?", (pid,)).fetchone()


def save_party(con, d: dict, pid=None, ptype="customer"):
    cols = ("name", "phone", "email", "address", "gstin", "price_list_id", "trade_discount", "state_code", "credit_limit", "currency")
    vals = [d.get(c) if c not in ("trade_discount", "credit_limit") else (d.get(c) or 0) for c in cols]
    if pid:
        con.execute(f"UPDATE parties SET {', '.join(c + '=?' for c in cols)} WHERE id=?", vals + [pid])
    else:
        pid = con.execute(f"INSERT INTO parties({', '.join(cols)}, type) VALUES({','.join('?' * len(cols))},?)",
                          vals + [ptype]).lastrowid
    con.commit()
    return pid


def delete_party(con, pid):
    used = con.execute(
        """SELECT (SELECT COUNT(*) FROM sales WHERE customer_id = :p) + (SELECT COUNT(*) FROM purchases WHERE supplier_id = :p)
                + (SELECT COUNT(*) FROM batches WHERE supplier_id = :p) + (SELECT COUNT(*) FROM payments WHERE party_id = :p)""",
        {"p": pid}).fetchone()[0]
    if used:
        raise ValueError("This party already has bills, purchases or payments, so it can't be deleted.")
    con.execute("DELETE FROM parties WHERE id = ?", (pid,))
    con.commit()


def record_payment(con, pid, amount, mode="cash", note="", currency=None, orig_amount=None):
    """Customer pays us / we pay a supplier: reduces the outstanding balance. `amount` is in the base currency; when the money was
    paid in another currency, pass that currency and the amount in it (orig_amount)."""
    from . import fy
    fy.guard(con)
    cur, rate = currency_mod.resolve(con, currency)
    if cur != currency_mod.base_code(con) and not orig_amount:
        orig_amount = currency_mod.from_base(amount, rate)
    pay_id = con.execute("INSERT INTO payments(party_id,amount,mode,note,currency,exchange_rate,orig_amount) VALUES(?,?,?,?,?,?,?)",
                         (pid, amount, mode, note, cur, rate, orig_amount if cur != currency_mod.base_code(con) else None)).lastrowid
    con.execute("UPDATE parties SET balance = balance - ? WHERE id = ?", (amount, pid))
    con.commit()
    return pay_id


# ---------- Stock in / out ----------

def latest_batch_prices(con, product_id):
    return con.execute("SELECT cost_price, mrp, sale_price FROM batches WHERE product_id=? ORDER BY id DESC LIMIT 1",
                       (product_id,)).fetchone()


def purchase_amounts(line):
    """(taxable, gst, total) of one purchase line. Cost price is entered without GST."""
    taxable = round(line["qty"] * line["cost_price"], 2)
    gst = round(taxable * line["gst"] / 100, 2)
    return taxable, gst, round(taxable + gst, 2)


def _total_qty(con, batch_id):
    r = con.execute("SELECT b.qty + COALESCE((SELECT SUM(qty) FROM godown_stock WHERE batch_id = b.id), 0) AS t "
                    "FROM batches b WHERE b.id = ?", (batch_id,)).fetchone()
    return r["t"] if r else 0


def create_purchase(con, supplier_id, invoice_no, invoice_date, lines, payment_method="cash", advance=None,
                    advance_method=None, godown_id=None, currency=None):
    """Stock IN with full GST detail. lines: [{product_id, batch_no, expiry_date, qty, cost_price, mrp,
    sale_price, gst, hsn}]. Same product + batch no. adds to the existing batch (cost becomes a
    weighted average). Saves purchases, purchase_items, batches and stock_movements in one transaction.
    godown_id: receive into that godown instead of the main shop (None = main shop)."""
    from . import fy
    fy.guard(con, invoice_date)
    cur, rate = currency_mod.resolve(con, currency)
    try:
        main_id = get_main_godown_id(con)
        into_godown = godown_id if godown_id not in (None, main_id) else None
        if invoice_no and supplier_id and con.execute(
                "SELECT 1 FROM purchases WHERE supplier_id=? AND bill_no=?", (supplier_id, invoice_no)).fetchone():
            raise ValueError(f"Invoice {invoice_no} from this supplier is already saved.")
        amounts = [purchase_amounts(l) for l in lines]
        taxable = round(sum(a[0] for a in amounts), 2)
        gst = round(sum(a[1] for a in amounts), 2)
        total = round(taxable + gst, 2)
        paid = total if advance is None else min(advance, total)
        due = round(total - paid, 2)
        if paid > 0 and advance_method is None and payment_method in ("cash", "upi", "card"):
            advance_method = payment_method
        if paid <= 0:
            advance_method = None
        pid = con.execute(
            """INSERT INTO purchases(bill_no,supplier_id,purchase_date,total,paid,taxable_value,gst_amount,
                                     payment_method,advance_amount,advance_method,balance_due,currency,exchange_rate,orig_total)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (invoice_no, supplier_id, invoice_date, total, paid, taxable, gst, payment_method, paid,
             advance_method, due, cur, rate, currency_mod.from_base(total, rate))).lastrowid
        for l, (tv, ga, lt) in zip(lines, amounts):
            b = con.execute("SELECT id, qty, cost_price FROM batches WHERE product_id=? AND batch_no=?",
                            (l["product_id"], l["batch_no"])).fetchone()
            main_qty = 0 if into_godown else l["qty"]
            if b:
                before = _total_qty(con, b["id"])
                cost = (before * b["cost_price"] + l["qty"] * l["cost_price"]) / (before + l["qty"])
                con.execute("""UPDATE batches SET qty=qty+?, cost_price=?, mrp=?, sale_price=?, supplier_id=?,
                               expiry_date=COALESCE(?, expiry_date) WHERE id=?""",
                            (main_qty, round(cost, 2), l["mrp"], l["sale_price"], supplier_id, l["expiry_date"], b["id"]))
                bid = b["id"]
            else:
                bid = con.execute(
                    """INSERT INTO batches(product_id,batch_no,expiry_date,qty,cost_price,mrp,sale_price,supplier_id)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (l["product_id"], l["batch_no"], l["expiry_date"], main_qty, l["cost_price"], l["mrp"],
                     l["sale_price"], supplier_id)).lastrowid
            con.execute(
                """INSERT INTO purchase_items(purchase_id,product_id,batch_id,qty,cost_price,mrp,gst_percent,hsn_code,
                                              expiry_date,taxable_value,gst_amount,line_total)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, l["product_id"], bid, l["qty"], l["cost_price"], l["mrp"], l["gst"], l.get("hsn") or None,
                 l["expiry_date"], tv, ga, lt))
            if into_godown:
                _godown_add(con, into_godown, bid, l["qty"])
                con.execute("INSERT INTO stock_transfers(batch_id,from_id,to_id,qty,note) VALUES(?,NULL,?,?,?)",
                            (bid, into_godown, l["qty"], f"Purchase {invoice_no}" if invoice_no else "Purchase"))
            else:
                con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,ref_id,note) VALUES(?,?,'purchase',?,?)",
                            (bid, l["qty"], pid, f"Invoice {invoice_no}" if invoice_no else None))
            if l.get("hsn"):
                con.execute("UPDATE products SET hsn_code = ? WHERE id = ? AND (hsn_code IS NULL OR hsn_code = '')",
                            (l["hsn"], l["product_id"]))
        if supplier_id and due > 0:
            con.execute("UPDATE parties SET balance = balance + ? WHERE id = ?", (due, supplier_id))
        con.commit()
        return pid
    except Exception:
        con.rollback()
        raise


def adjust_stock(con, batch_id, change, reason, note="", commit=True):
    """Stock OUT/IN outside of sales. reason: adjustment | expired | return."""
    from . import fy
    fy.guard(con)
    b = con.execute("SELECT qty FROM batches WHERE id=?", (batch_id,)).fetchone()
    if b["qty"] + change < 0:
        raise ValueError(f"Only {b['qty']} in stock - it can't go below zero.")
    con.execute("UPDATE batches SET qty = qty + ? WHERE id = ?", (change, batch_id))
    con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,note) VALUES(?,?,?,?)",
                (batch_id, change, reason, note or None))
    if commit:
        con.commit()


def expired_stock(con):
    """(batches, value at cost) of expired stock still on the shelves or in godowns."""
    r = con.execute(f"""SELECT COUNT(*) AS n, COALESCE(SUM({TOTAL_QTY} * b.cost_price),0) AS v FROM batches b
                        WHERE {TOTAL_QTY} > 0 AND b.expiry_date IS NOT NULL AND b.expiry_date < date('now','localtime')""").fetchone()
    return r["n"], r["v"]


def writeoff_expired(con):
    """Removes ALL expired stock, in the main shop and in every godown. Returns the number of batches."""
    rows = con.execute(f"""SELECT b.id, b.qty FROM batches b WHERE {TOTAL_QTY} > 0 AND b.expiry_date IS NOT NULL
                           AND b.expiry_date < date('now','localtime')""").fetchall()
    try:
        for r in rows:
            if r["qty"] > 0:
                adjust_stock(con, r["id"], -r["qty"], "expired", "Bulk write-off of expired stock", commit=False)
            for g in con.execute("SELECT godown_id, qty FROM godown_stock WHERE batch_id = ? AND qty > 0", (r["id"],)).fetchall():
                con.execute("UPDATE godown_stock SET qty = 0 WHERE godown_id = ? AND batch_id = ?", (g["godown_id"], r["id"]))
                con.execute("INSERT INTO stock_transfers(batch_id, from_id, to_id, qty, note) VALUES(?,?,NULL,?,?)",
                            (r["id"], g["godown_id"], g["qty"], "Expired write-off"))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(rows)


def batches_for_adjust(con):
    return con.execute(
        """SELECT b.id, p.name, b.batch_no, b.expiry_date, b.qty FROM batches b JOIN products p ON p.id = b.product_id
           WHERE p.active = 1 ORDER BY p.name, b.expiry_date""").fetchall()


def list_movements(con, search="", limit=300):
    like = f"%{search}%"
    return con.execute(
        """SELECT m.created_at, p.name, b.batch_no, m.change_qty, m.reason, m.note
           FROM stock_movements m JOIN batches b ON b.id = m.batch_id JOIN products p ON p.id = b.product_id
           WHERE p.name LIKE ? OR b.batch_no LIKE ? ORDER BY m.id DESC LIMIT ?""", (like, like, limit)).fetchall()


# ---------- Returns, credit notes & debit notes ----------

def recent_sales(con, limit=300):
    return con.execute(
        """SELECT s.id, s.invoice_no AS ref, s.sale_date AS doc_date, s.total, s.customer_id AS party_id,
                  COALESCE(c.name, 'Walk-in Customer') AS party_name, s.discount, s.subtotal, s.tax
           FROM sales s LEFT JOIN parties c ON c.id = s.customer_id WHERE s.cancelled = 0 ORDER BY s.id DESC LIMIT ?""", (limit,)).fetchall()


def recent_purchases(con, limit=300):
    return con.execute(
        """SELECT pu.id, COALESCE(NULLIF(pu.bill_no,''), 'Purchase #' || pu.id) AS ref, pu.purchase_date AS doc_date,
                  pu.total, pu.supplier_id AS party_id, COALESCE(s.name, '-') AS party_name, 0.0 AS discount,
                  pu.taxable_value AS subtotal, pu.gst_amount AS tax
           FROM purchases pu LEFT JOIN parties s ON s.id = pu.supplier_id ORDER BY pu.id DESC LIMIT ?""",
        (limit,)).fetchall()


def discount_ratio(doc):
    """Share of an invoice-level discount that must also be taken back on a return."""
    gross = (doc["subtotal"] or 0) + (doc["tax"] or 0)
    return (doc["discount"] or 0) / gross if gross else 0.0


def return_lines(con, kind, doc_id):
    """Lines of the original invoice/purchase with how many units can still be returned.
    Customer returns go back to the same batch they were sold from; supplier returns can
    only take out what is still in that batch."""
    if kind == "customer":
        rows = con.execute(
            """SELECT si.id, p.name, p.hsn_code, si.product_id, si.batch_id, b.batch_no, si.qty, si.gst_percent,
                      si.line_total AS taxable_value, ROUND(si.line_total * 1.0 / si.qty, 2) AS rate, b.qty AS in_stock,
                      COALESCE((SELECT SUM(ri.qty) FROM return_items ri JOIN returns r ON r.id = ri.return_id
                                WHERE ri.ref_item_id = si.id AND r.kind = 'customer'), 0) AS returned
               FROM sale_items si JOIN products p ON p.id = si.product_id JOIN batches b ON b.id = si.batch_id
               WHERE si.sale_id = ? ORDER BY si.id""", (doc_id,)).fetchall()
    else:
        rows = con.execute(
            """SELECT pi.id, p.name, COALESCE(pi.hsn_code, p.hsn_code) AS hsn_code, pi.product_id, pi.batch_id,
                      b.batch_no, pi.qty, pi.gst_percent, pi.taxable_value, pi.cost_price AS rate, b.qty AS in_stock,
                      COALESCE((SELECT SUM(ri.qty) FROM return_items ri JOIN returns r ON r.id = ri.return_id
                                WHERE ri.ref_item_id = pi.id AND r.kind = 'supplier'), 0) AS returned
               FROM purchase_items pi JOIN products p ON p.id = pi.product_id JOIN batches b ON b.id = pi.batch_id
               WHERE pi.purchase_id = ? ORDER BY pi.id""", (doc_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        left = r["qty"] - r["returned"]
        d["can_return"] = max(left, 0) if kind == "customer" else max(min(left, r["in_stock"]), 0)
        out.append(d)
    return out


def return_amounts(line, qty, ratio=0.0):
    """(taxable, gst) to reverse for `qty` units of an original line."""
    taxable = round(line["taxable_value"] / line["qty"] * qty * (1 - ratio), 2)
    return taxable, round(taxable * line["gst_percent"] / 100, 2)


def create_return(con, kind, doc_id, items, reason="", restock=True, refund_method="adjust"):
    """Customer return (credit note CN-) or supplier return (debit note DN-).
    items: [{ref_item_id, qty}]. Reverses stock and GST, and either adjusts the party's balance
    (refund_method='adjust') or records a cash/UPI/card refund. One transaction; raises ValueError."""
    from . import fy
    fy.guard(con)
    try:
        lines = {l["id"]: l for l in return_lines(con, kind, doc_id)}
        if kind == "customer":
            doc = get_sale(con, doc_id)
            ref_no, party, ratio = doc["invoice_no"], doc["customer_id"], discount_ratio(doc)
        else:
            doc = con.execute("SELECT * FROM purchases WHERE id = ?", (doc_id,)).fetchone()
            ref_no, party, ratio = doc["bill_no"] or f"Purchase #{doc_id}", doc["supplier_id"], 0.0
        picked = []
        for it in items:
            l, q = lines.get(it["ref_item_id"]), it["qty"]
            if not l or q <= 0:
                continue
            if q > l["can_return"]:
                raise ValueError(f"Only {l['can_return']} of {l['name']} can be returned.")
            picked.append((l, q) + return_amounts(l, q, ratio))
        if not picked:
            raise ValueError("Choose at least one item to return.")
        prefix = "CN" if kind == "customer" else "DN"
        n = con.execute("SELECT COUNT(*) FROM returns WHERE kind = ?", (kind,)).fetchone()[0] + 1
        while con.execute("SELECT 1 FROM returns WHERE note_no = ?", (f"{prefix}-{n:05d}",)).fetchone():
            n += 1
        note_no = f"{prefix}-{n:05d}"
        taxable = round(sum(p[2] for p in picked), 2)
        gst = round(sum(p[3] for p in picked), 2)
        total = round(taxable + gst, 2)
        restocked = 1 if (kind == "supplier" or restock) else 0
        rid = con.execute(
            """INSERT INTO returns(kind,note_no,ref_id,ref_no,party_id,reason,restocked,refund_method,
                                   taxable_value,gst_amount,total) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (kind, note_no, doc_id, ref_no, party, reason, restocked, refund_method, taxable, gst, total)).lastrowid
        for l, q, tv, ga in picked:
            con.execute(
                """INSERT INTO return_items(return_id,ref_item_id,product_id,batch_id,qty,unit_price,gst_percent,
                                            hsn_code,taxable_value,gst_amount,line_total) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, l["id"], l["product_id"], l["batch_id"], q, l["rate"], l["gst_percent"], l["hsn_code"],
                 tv, ga, round(tv + ga, 2)))
            if kind == "customer" and restocked:
                con.execute("UPDATE batches SET qty = qty + ? WHERE id = ?", (q, l["batch_id"]))
                con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,ref_id,note) VALUES(?,?,'return',?,?)",
                            (l["batch_id"], q, rid, f"Credit note {note_no}"))
            elif kind == "supplier":
                con.execute("UPDATE batches SET qty = qty - ? WHERE id = ?", (q, l["batch_id"]))
                con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,ref_id,note) VALUES(?,?,?,?,?)",
                            (l["batch_id"], -q, "expired" if reason == "Expired" else "return", rid,
                             f"Debit note {note_no}" + (f" - {reason}" if reason else "")))
        if refund_method == "adjust" and party:
            con.execute("UPDATE parties SET balance = balance - ? WHERE id = ?", (total, party))
        con.commit()
        return rid
    except Exception:
        con.rollback()
        raise


def get_note(con, rid):
    return con.execute(
        """SELECT r.*, COALESCE(pa.name, 'Walk-in Customer') AS party_name, pa.gstin AS party_gstin
           FROM returns r LEFT JOIN parties pa ON pa.id = r.party_id WHERE r.id = ?""", (rid,)).fetchone()


def get_note_items(con, rid):
    return con.execute(
        """SELECT p.name, ri.hsn_code, ri.qty, ri.unit_price, ri.gst_percent, ri.taxable_value, ri.gst_amount, ri.line_total
           FROM return_items ri JOIN products p ON p.id = ri.product_id WHERE ri.return_id = ? ORDER BY ri.id""",
        (rid,)).fetchall()


def list_notes(con, search="", limit=300):
    like = f"%{search}%"
    return con.execute(
        """SELECT r.id, r.kind, r.note_no, r.ref_no, r.created_at, r.total, r.refund_method,
                  COALESCE(pa.name, 'Walk-in Customer') AS party_name
           FROM returns r LEFT JOIN parties pa ON pa.id = r.party_id
           WHERE r.note_no LIKE ? OR IFNULL(r.ref_no,'') LIKE ? OR IFNULL(pa.name,'') LIKE ?
           ORDER BY r.id DESC LIMIT ?""", (like, like, like, limit)).fetchall()


# ---------- Ledgers ----------

def party_ledger(con, pid, d1=None, d2=None):
    """Chronological ledger rows [{date, particulars, debit, credit, balance}] for a customer or supplier.
    Customer: debit = invoices / refunds paid, credit = money received / credit notes.
    Supplier: credit = purchases / refunds received, debit = money paid / debit notes.
    Balance is always 'amount outstanding'; a first row carries any earlier balance not in these records."""
    p = get_party(con, pid)
    cust = p["type"] == "customer"
    ev = []                                           # (when, particulars, debit, credit)
    base = currency_mod.base_code(con)

    def fc(row, key):                                 # " [USD 12.00 @ 83.5]" for documents made in another currency
        cur = row["currency"]
        return f" [{currency_mod.show(cur, row[key])} @ {row['exchange_rate']:g}]" if cur and cur != base and row[key] else ""
    if cust:
        for s in con.execute("SELECT * FROM sales WHERE customer_id = ? AND cancelled = 0 ORDER BY id", (pid,)):
            ev.append((s["sale_date"], f"Invoice {s['invoice_no']}" + fc(s, "orig_total"), s["total"], 0.0, s["frozen"]))
            if (s["advance_amount"] or 0) > 0:
                ev.append((s["sale_date"], f"Received with bill ({(s['advance_method'] or 'cash').upper()})",
                           0.0, s["advance_amount"], s["frozen"]))
    else:
        for u in con.execute("SELECT * FROM purchases WHERE supplier_id = ? ORDER BY id", (pid,)):
            when = (u["purchase_date"] or "")[:10] + " 00:00:00"
            ev.append((when, f"Purchase {u['bill_no'] or '#' + str(u['id'])}" + fc(u, "orig_total"), 0.0, u["total"], u["frozen"]))
            if (u["advance_amount"] or 0) > 0:
                ev.append((when, f"Paid with purchase ({(u['advance_method'] or 'cash').upper()})",
                           u["advance_amount"], 0.0, u["frozen"]))
    for pay in con.execute("SELECT * FROM payments WHERE party_id = ? ORDER BY id", (pid,)):
        label = ("Payment received" if cust else "Payment made") + f" ({(pay['mode'] or 'cash').upper()})"
        ev.append((pay["paid_on"], label + fc(pay, "orig_amount") + (f" - {pay['note']}" if pay["note"] else ""),
                   0.0 if cust else pay["amount"], pay["amount"] if cust else 0.0, pay["frozen"]))
    for r in con.execute("SELECT * FROM returns WHERE party_id = ? AND kind = ? ORDER BY id",
                         (pid, "customer" if cust else "supplier")):
        ev.append((r["created_at"], f"{'Credit' if cust else 'Debit'} note {r['note_no']}",
                   0.0 if cust else r["total"], r["total"] if cust else 0.0, r["frozen"]))
        if r["refund_method"] != "adjust":            # cash changed hands, so the note is settled at once
            ev.append((r["created_at"], f"Refund {'paid' if cust else 'received'} ({r['refund_method'].upper()})",
                       r["total"] if cust else 0.0, 0.0 if cust else r["total"], r["frozen"]))
    ev = [e for _, e in sorted(enumerate(ev), key=lambda ie: (ie[1][0], ie[0]))]

    def delta(debit, credit):
        return (debit - credit) if cust else (credit - debit)

    rows, bal = [], 0.0
    gap = round(p["balance"] - sum(delta(e[2], e[3]) for e in ev), 2)
    if abs(gap) > 0.005:                              # balance that isn't explained by the records below
        d, c = (gap, 0.0) if (gap > 0) == cust else (0.0, abs(gap))
        ev.insert(0, ("", "Opening / earlier balance", abs(d), abs(c), 0))
    for when, text, debit, credit, frozen in ev:
        bal = round(bal + delta(debit, credit), 2)
        rows.append({"date": when[:10], "particulars": text, "debit": debit, "credit": credit, "balance": bal,
                     "frozen": bool(frozen)})
    if d1 or d2:                                      # one period only: earlier history collapses into an opening balance
        before = [r for r in rows if d1 and r["date"] < d1]
        inside = [r for r in rows if not (d1 and r["date"] < d1) and not (d2 and r["date"] > d2)]
        opening = before[-1]["balance"] if before else 0.0
        rows = ([{"date": d1 or "", "particulars": "Opening balance (carried forward)", "debit": 0.0, "credit": 0.0,
                  "balance": opening}] if d1 else []) + inside
    return rows


# ---------- Price lists, schemes ----------

def list_price_lists(con):
    return con.execute("SELECT * FROM price_lists ORDER BY id").fetchall()


def get_price_list(con, lid):
    return con.execute("SELECT * FROM price_lists WHERE id = ?", (lid,)).fetchone()


def save_price_list(con, name, basis, pct, lid=None):
    if lid:
        con.execute("UPDATE price_lists SET name=?, basis=?, adjust_percent=? WHERE id=?", (name, basis, pct, lid))
    else:
        lid = con.execute("INSERT INTO price_lists(name,basis,adjust_percent) VALUES(?,?,?)", (name, basis, pct)).lastrowid
    con.commit()
    return lid


def delete_price_list(con, lid):
    first = con.execute("SELECT MIN(id) FROM price_lists").fetchone()[0]
    if lid == first:
        raise ValueError("The first price list is the default for everyone and can't be deleted.")
    con.execute("UPDATE parties SET price_list_id = NULL WHERE price_list_id = ?", (lid,))
    con.execute("DELETE FROM price_lists WHERE id = ?", (lid,))
    con.commit()


def set_list_price(con, lid, product_id, price):
    """price=None removes the fixed price (the product falls back to basis +/- adjustment)."""
    if price is None:
        con.execute("DELETE FROM price_list_items WHERE list_id=? AND product_id=?", (lid, product_id))
    else:
        con.execute("""INSERT INTO price_list_items(list_id,product_id,price) VALUES(?,?,?)
                       ON CONFLICT(list_id,product_id) DO UPDATE SET price=excluded.price""", (lid, product_id, price))
    con.commit()


def list_overrides(con, lid):
    return {r["product_id"]: r["price"] for r in
            con.execute("SELECT product_id, price FROM price_list_items WHERE list_id = ?", (lid,))}


def list_price(plist, overrides, product_id, base_sale, base_mrp):
    """Price of a product under a price list: fixed price if set, else basis price +/- adjustment."""
    if product_id in overrides:
        return overrides[product_id]
    base = base_mrp if plist["basis"] == "mrp" else base_sale
    return round((base or 0) * (1 + plist["adjust_percent"] / 100), 2)


def price_list_for_customer(con, customer_id):
    """(price list row, overrides) that applies to this customer; the default list if none is assigned."""
    p = get_party(con, customer_id) if customer_id else None
    pl = get_price_list(con, p["price_list_id"]) if p and p["price_list_id"] else None
    if pl is None:
        pl = con.execute("SELECT * FROM price_lists ORDER BY id LIMIT 1").fetchone()
    return pl, list_overrides(con, pl["id"])


def products_with_base_prices(con):
    return con.execute(
        """SELECT p.id, p.name,
                  (SELECT sale_price FROM batches WHERE product_id = p.id ORDER BY id DESC LIMIT 1) AS sale_price,
                  (SELECT mrp FROM batches WHERE product_id = p.id ORDER BY id DESC LIMIT 1) AS mrp
           FROM products p WHERE p.active = 1 ORDER BY p.name""").fetchall()


def list_schemes(con):
    return con.execute("""SELECT s.*, p.name FROM schemes s JOIN products p ON p.id = s.product_id
                          ORDER BY p.name, s.id""").fetchall()


def save_scheme(con, product_id, buy, free):
    con.execute("INSERT INTO schemes(product_id,buy_qty,free_qty) VALUES(?,?,?)", (product_id, buy, free))
    con.commit()


def toggle_scheme(con, sid):
    con.execute("UPDATE schemes SET active = 1 - active WHERE id = ?", (sid,))
    con.commit()


def delete_scheme(con, sid):
    con.execute("DELETE FROM schemes WHERE id = ?", (sid,))
    con.commit()


def schemes_map(con):
    """{product_id: (buy, free)} for active schemes (if a product has several, the most generous wins)."""
    out = {}
    for s in con.execute("SELECT * FROM schemes WHERE active = 1 ORDER BY id"):
        cur = out.get(s["product_id"])
        if cur is None or s["free_qty"] / s["buy_qty"] >= cur[1] / cur[0]:
            out[s["product_id"]] = (s["buy_qty"], s["free_qty"])
    return out


def free_units(scheme, qty):
    """Buy X get Y free: how many free units does `qty` earn?"""
    if not scheme or scheme[0] <= 0:
        return 0
    return (qty // scheme[0]) * scheme[1]


# ---------- Dashboard trends ----------

def daily_sales(con, days=30):
    """[(date, total)] for each of the last `days` days, today last (zeros for quiet days)."""
    from datetime import date, timedelta
    got = {r["d"]: r["t"] for r in con.execute(
        """SELECT date(sale_date) AS d, SUM(total) AS t FROM sales
           WHERE cancelled = 0 AND date(sale_date) >= date('now','localtime', ?) GROUP BY d""", (f"-{days - 1} days",))}
    today = date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        out.append((d, got.get(d.isoformat(), 0.0)))
    return out


def top_customers(con, days=30, limit=5):
    return con.execute(
        """SELECT c.name, SUM(s.total) AS revenue, COUNT(*) AS bills
           FROM sales s JOIN parties c ON c.id = s.customer_id
           WHERE s.cancelled = 0 AND c.name != 'Walk-in Customer' AND date(s.sale_date) >= date('now','localtime', ?)
           GROUP BY c.id ORDER BY revenue DESC LIMIT ?""", (f"-{days - 1} days", limit)).fetchall()


def top_products_qty(con, days=30, limit=5):
    return con.execute(
        """SELECT p.name, SUM(CASE WHEN si.unit_price > 0 THEN si.qty ELSE 0 END) AS sold, SUM(si.line_total) AS revenue
           FROM sale_items si JOIN sales s ON s.id = si.sale_id JOIN products p ON p.id = si.product_id
           WHERE s.cancelled = 0 AND date(s.sale_date) >= date('now','localtime', ?)
           GROUP BY p.id HAVING sold > 0 ORDER BY sold DESC LIMIT ?""", (f"-{days - 1} days", limit)).fetchall()


def week_compare(con):
    """This week (last 7 days incl. today) vs the 7 days before: {metric: (this, last, % change or None)}."""
    def agg(a, b):
        r = con.execute(
            """SELECT COUNT(*) AS bills, COALESCE(SUM(total), 0) AS sales FROM sales
               WHERE cancelled = 0 AND date(sale_date) BETWEEN date('now','localtime', ?) AND date('now','localtime', ?)""",
            (f"{a:+d} days", f"{b:+d} days")).fetchone()
        profit = con.execute(
            """SELECT COALESCE(SUM(si.line_total - si.qty * si.cost_price), 0)
               FROM sale_items si JOIN sales s ON s.id = si.sale_id
               WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN date('now','localtime', ?) AND date('now','localtime', ?)""",
            (f"{a:+d} days", f"{b:+d} days")).fetchone()[0]
        return {"sales": r["sales"], "bills": r["bills"], "profit": profit}

    this, last = agg(-6, 0), agg(-13, -7)
    return {k: (this[k], last[k], ((this[k] - last[k]) / last[k] * 100) if last[k] else None) for k in this}


# ---------- Godowns (stock locations) ----------

def list_godowns(con):
    return con.execute("SELECT * FROM godowns ORDER BY is_main DESC, name").fetchall()


def get_main_godown_id(con):
    return con.execute("SELECT id FROM godowns WHERE is_main = 1 ORDER BY id LIMIT 1").fetchone()["id"]


def save_godown(con, name, gid=None):
    if gid:
        con.execute("UPDATE godowns SET name = ? WHERE id = ?", (name, gid))
    else:
        gid = con.execute("INSERT INTO godowns(name) VALUES(?)", (name,)).lastrowid
    con.commit()
    return gid


def delete_godown(con, gid):
    g = con.execute("SELECT * FROM godowns WHERE id = ?", (gid,)).fetchone()
    if g["is_main"]:
        raise ValueError("The main shop can't be deleted.")
    if con.execute("SELECT COALESCE(SUM(qty), 0) FROM godown_stock WHERE godown_id = ?", (gid,)).fetchone()[0] > 0:
        raise ValueError("This godown still has stock. Transfer it out first.")
    con.execute("DELETE FROM godown_stock WHERE godown_id = ?", (gid,))
    con.execute("UPDATE stock_transfers SET from_id = NULL WHERE from_id = ?", (gid,))
    con.execute("UPDATE stock_transfers SET to_id = NULL WHERE to_id = ?", (gid,))
    con.execute("DELETE FROM godowns WHERE id = ?", (gid,))
    con.commit()


def _godown_add(con, gid, batch_id, qty):
    con.execute("""INSERT INTO godown_stock(godown_id, batch_id, qty) VALUES(?,?,?)
                   ON CONFLICT(godown_id, batch_id) DO UPDATE SET qty = qty + excluded.qty""", (gid, batch_id, qty))


def location_qty(con, batch_id, godown_id):
    if godown_id == get_main_godown_id(con):
        r = con.execute("SELECT qty FROM batches WHERE id = ?", (batch_id,)).fetchone()
        return r["qty"] if r else 0
    r = con.execute("SELECT qty FROM godown_stock WHERE godown_id = ? AND batch_id = ?", (godown_id, batch_id)).fetchone()
    return r["qty"] if r else 0


def stock_by_location(con, godown_id=None):
    """Rows [{godown_id, godown, product, batch_id, batch_no, expiry_date, qty}] with stock > 0, for one
    location or all of them."""
    main = get_main_godown_id(con)
    out = []
    if godown_id in (None, main):
        for r in con.execute("""SELECT b.id AS batch_id, p.name AS product, b.batch_no, b.expiry_date, b.qty
                                FROM batches b JOIN products p ON p.id = b.product_id
                                WHERE b.qty > 0 AND p.active = 1 ORDER BY p.name, b.expiry_date"""):
            out.append({**dict(r), "godown_id": main, "godown": con.execute("SELECT name FROM godowns WHERE id=?", (main,)).fetchone()[0]})
    q = """SELECT g.id AS godown_id, g.name AS godown, b.id AS batch_id, p.name AS product, b.batch_no, b.expiry_date, gs.qty
           FROM godown_stock gs JOIN godowns g ON g.id = gs.godown_id JOIN batches b ON b.id = gs.batch_id
           JOIN products p ON p.id = b.product_id WHERE gs.qty > 0 AND p.active = 1"""
    args = ()
    if godown_id not in (None, main):
        q += " AND g.id = ?"
        args = (godown_id,)
    if godown_id is None or godown_id != main:
        out += [dict(r) for r in con.execute(q + " ORDER BY g.name, p.name, b.expiry_date", args)]
    return out


def transfer_stock(con, batch_id, from_id, to_id, qty, note=""):
    """Move stock between locations. Selling happens from the main shop, so moving stock to the main shop
    makes it sellable."""
    if from_id == to_id:
        raise ValueError("Choose two different locations.")
    if qty <= 0:
        raise ValueError("Enter a quantity.")
    have = location_qty(con, batch_id, from_id)
    if qty > have:
        raise ValueError(f"Only {have} available in the source location.")
    main = get_main_godown_id(con)
    names = {g["id"]: g["name"] for g in list_godowns(con)}
    try:
        if from_id == main:
            con.execute("UPDATE batches SET qty = qty - ? WHERE id = ?", (qty, batch_id))
            con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,note) VALUES(?,?,'transfer',?)",
                        (batch_id, -qty, f"To {names[to_id]}"))
        else:
            con.execute("UPDATE godown_stock SET qty = qty - ? WHERE godown_id = ? AND batch_id = ?", (qty, from_id, batch_id))
        if to_id == main:
            con.execute("UPDATE batches SET qty = qty + ? WHERE id = ?", (qty, batch_id))
            con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,note) VALUES(?,?,'transfer',?)",
                        (batch_id, qty, f"From {names[from_id]}"))
        else:
            _godown_add(con, to_id, batch_id, qty)
        con.execute("INSERT INTO stock_transfers(batch_id,from_id,to_id,qty,note) VALUES(?,?,?,?,?)",
                    (batch_id, from_id, to_id, qty, note or None))
        con.commit()
    except Exception:
        con.rollback()
        raise


def list_transfers(con, limit=200):
    return con.execute(
        """SELECT t.created_at, p.name AS product, b.batch_no, t.qty, COALESCE(f.name, 'Supplier') AS from_name,
                  COALESCE(g.name, 'Written off') AS to_name, t.note
           FROM stock_transfers t JOIN batches b ON b.id = t.batch_id JOIN products p ON p.id = b.product_id
           LEFT JOIN godowns f ON f.id = t.from_id LEFT JOIN godowns g ON g.id = t.to_id
           ORDER BY t.id DESC LIMIT ?""", (limit,)).fetchall()


def low_stock_by_location(con):
    """[(location, [(product, qty here, reorder level, qty elsewhere)])]: products that are low at one location.
    Only counts sellable (not expired) stock. A godown is only checked for products it has held before."""
    main = get_main_godown_id(con)
    gods = list_godowns(con)
    ok = "(b.expiry_date IS NULL OR b.expiry_date >= date('now','localtime'))"
    prods = {p["id"]: p for p in con.execute("SELECT id, name, reorder_level FROM products WHERE active = 1")}
    here = {}                                   # (godown, product) -> qty
    for r in con.execute(f"SELECT b.product_id AS pid, SUM(b.qty) AS q FROM batches b WHERE {ok} GROUP BY b.product_id"):
        here[(main, r["pid"])] = r["q"]
    held = set((main, pid) for pid in prods)
    levels = {(r["godown_id"], r["product_id"]): r["level"] for r in con.execute("SELECT * FROM godown_reorder")}
    for r in con.execute(f"""SELECT gs.godown_id AS gid, b.product_id AS pid, SUM(CASE WHEN {ok} THEN gs.qty ELSE 0 END) AS q
                             FROM godown_stock gs JOIN batches b ON b.id = gs.batch_id GROUP BY gs.godown_id, b.product_id"""):
        here[(r["gid"], r["pid"])] = r["q"]
        held.add((r["gid"], r["pid"]))
    out = []
    for g in gods:
        rows = []
        for (gid, pid) in sorted(held, key=lambda k: prods.get(k[1], {"name": ""})["name"] if k[1] in prods else ""):
            if gid != g["id"] or pid not in prods:
                continue
            q, lvl = here.get((gid, pid), 0), levels.get((gid, pid), prods[pid]["reorder_level"])
            if q <= lvl:
                elsewhere = sum(v for (g2, p2), v in here.items() if p2 == pid and g2 != gid)
                rows.append((prods[pid]["name"], q, lvl, elsewhere))
        out.append((g["name"], rows))
    return out

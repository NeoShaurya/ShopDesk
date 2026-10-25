"""Extra billing and stock features: invoice series, price history, credit limits, cancelled bills,
per-location reorder levels."""
import re
from datetime import datetime

from . import audit, fy

SQL = """
CREATE TABLE IF NOT EXISTS invoice_series (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    prefix     TEXT UNIQUE NOT NULL,              -- e.g. 'RET-'
    digits     INTEGER NOT NULL DEFAULT 3,        -- RET-001
    next_no    INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS godown_reorder (       -- reorder level of one product at one location (overrides the product's level)
    godown_id  INTEGER NOT NULL REFERENCES godowns(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    level      INTEGER NOT NULL,
    PRIMARY KEY (godown_id, product_id)
);
CREATE TABLE IF NOT EXISTS price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    batch_id   INTEGER NOT NULL,
    batch_no   TEXT,
    cost_price REAL, mrp REAL, sale_price REAL,
    why        TEXT NOT NULL,                     -- 'new batch' | 'changed'
    changed_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, id);
"""

TRIGGERS = """
DROP TRIGGER IF EXISTS ph_insert;
CREATE TRIGGER ph_insert AFTER INSERT ON batches BEGIN
  INSERT INTO price_history(product_id,batch_id,batch_no,cost_price,mrp,sale_price,why)
  VALUES(NEW.product_id, NEW.id, NEW.batch_no, NEW.cost_price, NEW.mrp, NEW.sale_price, 'new batch');
END;
DROP TRIGGER IF EXISTS ph_update;
CREATE TRIGGER ph_update AFTER UPDATE OF cost_price, mrp, sale_price ON batches
WHEN OLD.cost_price IS NOT NEW.cost_price OR OLD.mrp IS NOT NEW.mrp OR OLD.sale_price IS NOT NEW.sale_price BEGIN
  INSERT INTO price_history(product_id,batch_id,batch_no,cost_price,mrp,sale_price,why)
  VALUES(NEW.product_id, NEW.id, NEW.batch_no, NEW.cost_price, NEW.mrp, NEW.sale_price, 'changed');
END;
"""


def install(con):
    from .db import _add_column
    _add_column(con, "sales", "cancelled", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "sales", "cancelled_at", "TEXT")
    _add_column(con, "sales", "cancel_reason", "TEXT")
    _add_column(con, "parties", "credit_limit", "REAL NOT NULL DEFAULT 0")
    con.executescript(SQL)
    if not con.execute("SELECT 1 FROM invoice_series").fetchone():
        nxt = con.execute("SELECT COALESCE(MAX(id),0)+1 FROM sales").fetchone()[0]
        con.execute("INSERT INTO invoice_series(name,prefix,digits,next_no,is_default) VALUES('Bills','INV-',5,?,1)", (nxt,))
    if not con.execute("SELECT 1 FROM price_history").fetchone():           # first start with this feature: record today's prices
        con.execute("""INSERT INTO price_history(product_id,batch_id,batch_no,cost_price,mrp,sale_price,why,changed_at)
                       SELECT product_id, id, batch_no, cost_price, mrp, sale_price, 'new batch',
                              COALESCE(received_on, date('now','localtime')) FROM batches""")
    con.executescript(TRIGGERS)
    con.commit()


# ---------------------------------------------------------------- invoice series
def series_list(con):
    return con.execute("SELECT * FROM invoice_series ORDER BY is_default DESC, name").fetchall()


def default_series_id(con):
    return con.execute("SELECT id FROM invoice_series ORDER BY is_default DESC, id LIMIT 1").fetchone()[0]


def example(prefix, digits, n):
    return f"{prefix}{n:0{int(digits)}d}"


def save_series(con, name, prefix, digits, next_no, make_default=False, sid=None):
    name, prefix = (name or "").strip(), (prefix or "").strip().upper()
    if not name:
        raise ValueError("Give the series a name, for example Retail.")
    if not prefix or not re.fullmatch(r"[A-Z0-9/-]+", prefix):
        raise ValueError("The prefix can use only letters, digits, - and /, for example RET-")
    if not 1 <= int(digits) <= 10:
        raise ValueError("Digits must be between 1 and 10.")
    if len(example(prefix, digits, int(next_no))) > 16:
        raise ValueError("An invoice number can be at most 16 characters (GST rule). Use a shorter prefix or fewer digits.")
    if int(next_no) < 1:
        raise ValueError("The next number must be 1 or more.")
    for col, val, label in (("name", name, "name"), ("prefix", prefix, "prefix")):
        if con.execute(f"SELECT 1 FROM invoice_series WHERE {col} = ? AND id IS NOT ?", (val, sid)).fetchone():
            raise ValueError(f"Another series already uses that {label}.")
    if sid and con.execute("SELECT prefix FROM invoice_series WHERE id=?", (sid,)).fetchone()["prefix"] != prefix and \
            con.execute("SELECT 1 FROM sales WHERE invoice_no LIKE ? ESCAPE '\\' LIMIT 1", (prefix.replace("_", "\\_") + "%",)).fetchone():
        raise ValueError("Bills already exist with that prefix. Choose a different prefix.")
    try:
        if make_default:
            con.execute("UPDATE invoice_series SET is_default = 0")
        if sid:
            con.execute("UPDATE invoice_series SET name=?, prefix=?, digits=?, next_no=?, is_default=CASE WHEN ? THEN 1 ELSE is_default END WHERE id=?",
                        (name, prefix, int(digits), int(next_no), 1 if make_default else 0, sid))
        else:
            sid = con.execute("INSERT INTO invoice_series(name,prefix,digits,next_no,is_default) VALUES(?,?,?,?,?)",
                              (name, prefix, int(digits), int(next_no), 1 if make_default else 0)).lastrowid
        if not con.execute("SELECT 1 FROM invoice_series WHERE is_default = 1").fetchone():
            con.execute("UPDATE invoice_series SET is_default = 1 WHERE id = ?", (sid,))
        audit.note(con, "invoice_series", "update", f"Invoice series '{name}': {example(prefix, digits, int(next_no))} next")
        con.commit()
    except Exception:
        con.rollback()
        raise
    return sid


def delete_series(con, sid):
    row = con.execute("SELECT * FROM invoice_series WHERE id=?", (sid,)).fetchone()
    if row["is_default"]:
        raise ValueError("The default series cannot be deleted. Make another series the default first.")
    con.execute("DELETE FROM invoice_series WHERE id=?", (sid,))
    audit.note(con, "invoice_series", "delete", f"Invoice series '{row['name']}' deleted")
    con.commit()


def take_number(con, series_id=None):
    """Next unused invoice number of the series, and moves the counter on. Does not commit (the sale does)."""
    s = con.execute("SELECT * FROM invoice_series WHERE id = ?", (series_id or default_series_id(con),)).fetchone()
    n = s["next_no"]
    while con.execute("SELECT 1 FROM sales WHERE invoice_no = ?", (example(s["prefix"], s["digits"], n),)).fetchone():
        n += 1
    con.execute("UPDATE invoice_series SET next_no = ? WHERE id = ?", (n + 1, s["id"]))
    return example(s["prefix"], s["digits"], n)


# ---------------------------------------------------------------- price history
def price_history(con, product_id):
    return con.execute("""SELECT changed_at, batch_no, cost_price, mrp, sale_price, why FROM price_history
                          WHERE product_id = ? ORDER BY id DESC""", (product_id,)).fetchall()


# ---------------------------------------------------------------- credit limit
def credit_check(con, customer_id, new_due):
    """None when fine, else {limit, balance, after, over}: the customer would go past the credit limit."""
    if not customer_id or new_due <= 0.005:
        return None
    p = con.execute("SELECT name, balance, credit_limit FROM parties WHERE id = ?", (customer_id,)).fetchone()
    if not p or (p["credit_limit"] or 0) <= 0:
        return None
    after = round(p["balance"] + new_due, 2)
    if after > p["credit_limit"] + 0.005:
        return {"name": p["name"], "limit": p["credit_limit"], "balance": p["balance"], "after": after,
                "over": round(after - p["credit_limit"], 2)}
    return None


def credit_message(c):
    return (f"{c['name']} has a credit limit of ₹{c['limit']:,.2f} and already owes ₹{c['balance']:,.2f}.\n"
            f"This bill would take it to ₹{c['after']:,.2f}, which is ₹{c['over']:,.2f} over the limit.")


def credit_action(con):
    from .db import get_settings
    return "block" if get_settings(con).get("credit_limit_action") == "block" else "warn"


# ---------------------------------------------------------------- cancel a bill
def cancel_sale(con, sale_id, reason):
    """Cancels (never deletes) a bill: stock goes back, the customer's balance is reduced, and the bill drops out of
    reports, GST and the books. The invoice number stays used and the audit log records who/what/why."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Please give a reason for cancelling the bill.")
    s = con.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not s:
        raise ValueError("Bill not found.")
    if s["cancelled"]:
        raise ValueError("This bill is already cancelled.")
    if s["frozen"]:
        raise ValueError("This bill is frozen, so it cannot be cancelled.")
    fy.guard(con, s["sale_date"])
    if con.execute("SELECT 1 FROM returns WHERE kind='customer' AND ref_id = ?", (sale_id,)).fetchone():
        raise ValueError("A credit note was already made against this bill. Cancel that first, or keep the bill.")
    try:
        for it in con.execute("SELECT batch_id, SUM(qty) AS q FROM sale_items WHERE sale_id = ? GROUP BY batch_id", (sale_id,)).fetchall():
            con.execute("UPDATE batches SET qty = qty + ? WHERE id = ?", (it["q"], it["batch_id"]))
            con.execute("INSERT INTO stock_movements(batch_id,change_qty,reason,ref_id,note) VALUES(?,?,'cancel',?,?)",
                        (it["batch_id"], it["q"], sale_id, f"Bill {s['invoice_no']} cancelled"))
        if s["customer_id"] and (s["balance_due"] or 0) > 0:
            con.execute("UPDATE parties SET balance = balance - ? WHERE id = ?", (s["balance_due"], s["customer_id"]))
        con.execute("UPDATE sales SET cancelled = 1, cancelled_at = ?, cancel_reason = ? WHERE id = ?",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reason, sale_id))
        audit.note(con, "sales", "update", f"Bill {s['invoice_no']} cancelled (₹{s['total']:,.2f}). Reason: {reason}")
        con.commit()
    except Exception:
        con.rollback()
        raise


def last_bill(con):
    r = con.execute("SELECT id FROM sales WHERE cancelled = 0 ORDER BY id DESC LIMIT 1").fetchone()
    return r["id"] if r else None


# ---------------------------------------------------------------- reorder level per location
def location_levels(con, godown_id):
    return {r["product_id"]: r["level"] for r in con.execute("SELECT product_id, level FROM godown_reorder WHERE godown_id = ?", (godown_id,))}


def set_location_level(con, godown_id, product_id, level):
    """level None = use the product's own level at this location."""
    if level is None:
        con.execute("DELETE FROM godown_reorder WHERE godown_id=? AND product_id=?", (godown_id, product_id))
    else:
        con.execute("INSERT INTO godown_reorder(godown_id,product_id,level) VALUES(?,?,?) "
                    "ON CONFLICT(godown_id,product_id) DO UPDATE SET level = excluded.level", (godown_id, product_id, int(level)))

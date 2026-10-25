"""Multi-currency. All amounts in the database (bills, purchases, payments, stock, GST, reports) are kept in the BASE
currency (INR unless changed). A document made in another currency also remembers the currency, the exchange rate used that
day and the amount in that currency, so both are shown on the bill and in the foreign-currency report, and old documents
never change when you update today's rate.
Rates mean: 1 unit of the currency = `rate` units of the base currency (1 USD = 83.50 INR)."""
import re
from datetime import date

from . import audit

SQL = """
CREATE TABLE IF NOT EXISTS currencies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    symbol     TEXT NOT NULL DEFAULT '',
    rate       REAL NOT NULL CHECK(rate > 0),
    updated_on TEXT NOT NULL DEFAULT (date('now','localtime'))
);
"""


def install(con):
    from .db import _add_column
    for table, cols in (("sales", ("orig_total",)), ("purchases", ("orig_total",)), ("payments", ("orig_amount",))):
        _add_column(con, table, "currency", "TEXT")
        _add_column(con, table, "exchange_rate", "REAL NOT NULL DEFAULT 1")
        for c in cols:
            _add_column(con, table, c, "REAL")
    _add_column(con, "parties", "currency", "TEXT")
    con.executescript(SQL)
    base = base_code(con)
    if not con.execute("SELECT 1 FROM currencies WHERE code = ?", (base,)).fetchone():
        con.execute("INSERT INTO currencies(code,name,symbol,rate) VALUES(?,?,?,1)",
                    (base, "Indian Rupee" if base == "INR" else base, "₹" if base == "INR" else base))
    con.commit()


def base_code(con):
    r = con.execute("SELECT value FROM settings WHERE key = 'base_currency'").fetchone()
    return (r["value"] if r and r["value"] else "INR")


def listing(con):
    b = base_code(con)
    return con.execute("SELECT * FROM currencies ORDER BY (code != ?), code", (b,)).fetchall()


def has_foreign(con):
    return len(con.execute("SELECT code FROM currencies").fetchall()) > 1


def resolve(con, code):
    """(code, rate) for a document. None or the base currency -> (base, 1.0)."""
    b = base_code(con)
    if not code or code == b:
        return b, 1.0
    r = con.execute("SELECT rate FROM currencies WHERE code = ?", (code,)).fetchone()
    if not r:
        raise ValueError(f"The currency {code} is not set up. Add it under Settings > Currencies.")
    return code, float(r["rate"])


def symbol(con, code):
    r = con.execute("SELECT symbol FROM currencies WHERE code = ?", (code,)).fetchone()
    return (r["symbol"] if r and r["symbol"] else code)


def to_base(amount, rate):
    return round(amount * rate, 2)


def from_base(amount, rate):
    return round(amount / rate, 2) if rate else amount


def show(code, amount):
    return f"{code} {amount:,.2f}"


def save_currency(con, code, name, sym, rate, old_code=None):
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError("The currency code is 3 letters, for example USD.")
    if not (name or "").strip():
        raise ValueError("Enter the currency's name, for example US Dollar.")
    base = base_code(con)
    rate = 1.0 if code == base else float(rate or 0)
    if rate <= 0:
        raise ValueError("The exchange rate must be more than 0.")
    if old_code and old_code != code:
        raise ValueError("A currency's code cannot be changed. Delete it and add the new one.")
    exists = con.execute("SELECT 1 FROM currencies WHERE code = ?", (code,)).fetchone()
    if exists:
        con.execute("UPDATE currencies SET name=?, symbol=?, rate=?, updated_on=date('now','localtime') WHERE code=?",
                    (name.strip(), (sym or "").strip(), rate, code))
    else:
        con.execute("INSERT INTO currencies(code,name,symbol,rate) VALUES(?,?,?,?)", (code, name.strip(), (sym or "").strip(), rate))
    con.commit()


def in_use(con, code):
    return any(con.execute(f"SELECT 1 FROM {t} WHERE currency = ? LIMIT 1", (code,)).fetchone()
               for t in ("sales", "purchases", "payments", "parties"))


def delete_currency(con, code):
    if code == base_code(con):
        raise ValueError("The base currency cannot be deleted.")
    if in_use(con, code):
        raise ValueError(f"{code} is already used by bills, purchases, payments or a party, so it cannot be deleted.")
    con.execute("DELETE FROM currencies WHERE code = ?", (code,))
    con.commit()


def has_transactions(con):
    return any(con.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in ("sales", "purchases", "payments", "expenses"))


def set_base(con, code):
    """Changes the base currency. Only before the first entry: every stored amount is in the base currency."""
    code = (code or "").strip().upper()
    if code == base_code(con):
        return
    if has_transactions(con):
        raise ValueError("The base currency cannot be changed once bills, purchases, payments or expenses exist, because all "
                         "of their amounts are kept in it.")
    if not con.execute("SELECT 1 FROM currencies WHERE code = ?", (code,)).fetchone():
        raise ValueError("Add that currency first.")
    con.execute("INSERT INTO settings(key,value) VALUES('base_currency',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (code,))
    con.execute("UPDATE currencies SET rate = 1 WHERE code = ?", (code,))
    audit.note(con, "currencies", "update", f"Base currency changed to {code}")
    con.commit()

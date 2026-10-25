"""Financial years (1 April - 31 March). Every transaction is tagged with its year; a closed year is read-only.

The tag is added by database triggers, and so is the read-only rule, so no screen can get around it.
Balances carry forward by themselves: a ledger's opening balance for a year is everything before its start date."""
from datetime import date, datetime

TAGGED = {  # table -> date column (items follow their parent bill)
    "sales": "sale_date", "purchases": "purchase_date", "returns": "created_at", "payments": "paid_on",
    "stock_movements": "created_at", "expenses": "spent_on",
}
CHILD = {"sale_items": ("sales", "sale_id", "sale_date"), "purchase_items": ("purchases", "purchase_id", "purchase_date"),
         "return_items": ("returns", "return_id", "created_at")}
CLOSED_MSG = "This financial year is closed, so its records cannot be changed. Reopen it in Financial Years if you must."


def bounds(d):
    """(start, end, label) of the Indian financial year containing date `d`."""
    y = d.year if d.month >= 4 else d.year - 1
    return date(y, 4, 1), date(y + 1, 3, 31), f"{y}-{str(y + 1)[2:]}"


def _d(s):
    return s if isinstance(s, date) and not isinstance(s, datetime) else datetime.fromisoformat(str(s)[:19]).date() \
        if len(str(s)) > 10 else date.fromisoformat(str(s))


def year_for(con, when=None):
    """The financial_years row containing `when` (a date or ISO string; default today). Creates it if missing."""
    d = _d(when) if when else date.today()
    row = con.execute("SELECT * FROM financial_years WHERE ? BETWEEN start_date AND end_date", (d.isoformat(),)).fetchone()
    if row:
        return row
    s, e, label = bounds(d)
    con.execute("INSERT OR IGNORE INTO financial_years(label, start_date, end_date) VALUES(?,?,?)",
                (label, s.isoformat(), e.isoformat()))
    con.commit()
    return con.execute("SELECT * FROM financial_years WHERE label = ?", (label,)).fetchone()


def guard(con, when=None):
    """Raises ValueError (plain message) when `when` falls in a closed year."""
    row = year_for(con, when)
    if row["status"] == "closed":
        raise ValueError(f"Financial year {row['label']} is closed, so this cannot be saved in it. "
                         "Use a date in an open year, or reopen the year in Financial Years.")
    return row


def current(con):
    return year_for(con)


def listing(con):
    return con.execute("SELECT * FROM financial_years ORDER BY start_date DESC").fetchall()


def sync(con):
    """Makes sure a year exists for every transaction date and for today, and tags untagged records."""
    lo = hi = date.today()
    for t, col in TAGGED.items():
        r = con.execute(f"SELECT MIN(date({col})), MAX(date({col})) FROM {t} WHERE {col} IS NOT NULL").fetchone()
        if r[0]:
            lo, hi = min(lo, date.fromisoformat(r[0])), max(hi, date.fromisoformat(r[1]))
    d = lo
    while d <= hi:
        year_for(con, d)
        d = date(bounds(d)[1].year, 4, 1)
    for t, col in TAGGED.items():
        con.execute(f"UPDATE {t} SET financial_year_id = (SELECT id FROM financial_years WHERE date({t}.{col}) "
                    f"BETWEEN start_date AND end_date) WHERE financial_year_id IS NULL")
    con.commit()


def new_next(con):
    """Creates the year after the latest one (if the latest has not been followed yet)."""
    last = con.execute("SELECT * FROM financial_years ORDER BY start_date DESC LIMIT 1").fetchone()
    return year_for(con, date.fromisoformat(last["end_date"]).replace(year=date.fromisoformat(last["end_date"]).year + 1))


def close(con, yid):
    row = con.execute("SELECT * FROM financial_years WHERE id=?", (yid,)).fetchone()
    if row["status"] == "closed":
        raise ValueError("This year is already closed.")
    if row["end_date"] >= date.today().isoformat():
        raise ValueError(f"Year {row['label']} has not ended yet (it ends on {row['end_date']}). Close it after that date.")
    year_for(con, date.fromisoformat(row["end_date"]).replace(year=date.fromisoformat(row["end_date"]).year + 1))
    con.execute("UPDATE financial_years SET status='closed', closed_at=datetime('now','localtime') WHERE id=?", (yid,))
    con.commit()


def reopen(con, yid):
    con.execute("UPDATE financial_years SET status='open', closed_at=NULL WHERE id=?", (yid,))
    con.commit()


def install(con):
    """Adds the year tag columns and (re)creates the triggers that tag records and lock closed years."""
    from .db import _add_column
    for t in TAGGED:
        _add_column(con, t, "financial_year_id", "INTEGER")
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'fy\\_%' ESCAPE '\\'").fetchall():
        con.execute(f"DROP TRIGGER {name}")
    closed = lambda expr: (f"EXISTS(SELECT 1 FROM financial_years WHERE status = 'closed' AND date({expr}) "
                           f"BETWEEN start_date AND end_date)")
    block = f"BEGIN SELECT RAISE(ABORT, '{CLOSED_MSG}'); END;"
    for t, col in TAGGED.items():
        con.execute(f"CREATE TRIGGER fy_tag_{t} AFTER INSERT ON {t} WHEN new.financial_year_id IS NULL BEGIN "
                    f"UPDATE {t} SET financial_year_id = (SELECT id FROM financial_years WHERE date(new.{col}) "
                    f"BETWEEN start_date AND end_date) WHERE id = new.id; END;")
        con.execute(f"CREATE TRIGGER fy_lock_{t}_ins BEFORE INSERT ON {t} WHEN {closed('new.' + col)} {block}")
        con.execute(f"CREATE TRIGGER fy_lock_{t}_upd BEFORE UPDATE ON {t} WHEN {closed('old.' + col)} {block}")
        con.execute(f"CREATE TRIGGER fy_lock_{t}_del BEFORE DELETE ON {t} WHEN {closed('old.' + col)} {block}")
    for t, (parent, fk, col) in CHILD.items():
        in_closed = lambda r: closed(f"(SELECT {col} FROM {parent} WHERE id = {r}.{fk})")
        con.execute(f"CREATE TRIGGER fy_lock_{t}_ins BEFORE INSERT ON {t} WHEN {in_closed('new')} {block}")
        con.execute(f"CREATE TRIGGER fy_lock_{t}_upd BEFORE UPDATE ON {t} WHEN {in_closed('old')} {block}")
        con.execute(f"CREATE TRIGGER fy_lock_{t}_del BEFORE DELETE ON {t} WHEN {in_closed('old')} {block}")
    con.commit()
    sync(con)

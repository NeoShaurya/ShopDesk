"""Ledger freezing: a frozen bill, purchase, return or payment can never be edited or deleted.

The rule lives in database triggers, so no screen or future feature can bypass it. Freezing is permanent."""
KINDS = {  # key -> (table, label, date column, reference SQL, party SQL, amount column)
    "sales": ("sales", "Bills", "sale_date", "t.invoice_no", "COALESCE((SELECT name FROM parties WHERE id=t.customer_id),'Walk-in Customer')", "t.total"),
    "purchases": ("purchases", "Purchases", "purchase_date", "COALESCE(NULLIF(t.bill_no,''),'#'||t.id)",
                  "COALESCE((SELECT name FROM parties WHERE id=t.supplier_id),'Cash purchase')", "t.total"),
    "returns": ("returns", "Credit / debit notes", "created_at", "t.note_no", "COALESCE((SELECT name FROM parties WHERE id=t.party_id),'Walk-in Customer')", "t.total"),
    "payments": ("payments", "Payments", "paid_on", "COALESCE(t.mode,'') || ' ' || COALESCE(t.note,'')",
                 "(SELECT name FROM parties WHERE id=t.party_id)", "t.amount"),
    "expenses": ("expenses", "Expenses", "spent_on", "t.category", "COALESCE(t.note,'')", "t.amount"),
}
CHILD = {"sale_items": ("sales", "sale_id"), "purchase_items": ("purchases", "purchase_id"), "return_items": ("returns", "return_id")}
MSG = "This entry is frozen, so it cannot be edited or deleted."


def install(con):
    from .db import _add_column
    for t in KINDS:
        _add_column(con, t, "frozen", "INTEGER NOT NULL DEFAULT 0")
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'frz\\_%' ESCAPE '\\'").fetchall():
        con.execute(f"DROP TRIGGER {name}")
    block = f"BEGIN SELECT RAISE(ABORT, '{MSG}'); END;"
    for t in KINDS:
        con.execute(f"CREATE TRIGGER frz_{t}_upd BEFORE UPDATE ON {t} WHEN old.frozen = 1 {block}")
        con.execute(f"CREATE TRIGGER frz_{t}_del BEFORE DELETE ON {t} WHEN old.frozen = 1 {block}")
    for t, (parent, fk) in CHILD.items():
        for ev, r in (("ins", "new"), ("upd", "old"), ("del", "old")):
            con.execute(f"CREATE TRIGGER frz_{t}_{ev} BEFORE {'INSERT' if ev == 'ins' else 'UPDATE' if ev == 'upd' else 'DELETE'} "
                        f"ON {t} WHEN (SELECT frozen FROM {parent} WHERE id = {r}.{fk}) = 1 {block}")
    con.commit()


def entries(con, kind, d1=None, d2=None, state=None):
    """Rows for the freeze screen: id, date, ref, party, amount, frozen."""
    table, _, col, ref, party, amt = KINDS[kind]
    sql = f"SELECT t.id, date(t.{col}) AS day, {ref} AS ref, {party} AS party, {amt} AS amount, t.frozen FROM {table} t WHERE 1=1"
    args = []
    if d1:
        sql += f" AND date(t.{col}) >= ?"; args.append(d1)
    if d2:
        sql += f" AND date(t.{col}) <= ?"; args.append(d2)
    if state == "open":
        sql += " AND t.frozen = 0"
    elif state == "frozen":
        sql += " AND t.frozen = 1"
    return con.execute(sql + f" ORDER BY t.{col}, t.id", args).fetchall()


def _skip_closed(table, col):
    # entries in a closed financial year are already read-only; leave them alone
    return (f" AND NOT EXISTS(SELECT 1 FROM financial_years y WHERE y.status='closed' AND date({table}.{col}) "
            f"BETWEEN y.start_date AND y.end_date)")


def freeze_ids(con, kind, ids):
    from . import audit
    table, label, col, *_ = KINDS[kind]
    if not ids:
        return 0
    try:
        marks = ",".join("?" * len(ids))
        n = con.execute(f"UPDATE {table} SET frozen = 1 WHERE frozen = 0 AND id IN ({marks})" + _skip_closed(table, col), ids).rowcount
        if n:
            audit.note(con, table, "freeze", f"Froze {n} {label.lower()} entr{'y' if n == 1 else 'ies'} by selection")
        con.commit()
        return n
    except Exception:
        con.rollback()
        raise


def freeze_upto(con, upto, kinds=None):
    """Freezes everything dated on or before `upto` (ISO date). Returns {kind: count}."""
    from . import audit
    out = {}
    try:
        for kind in (kinds or KINDS):
            table, label, col, *_ = KINDS[kind]
            out[kind] = con.execute(f"UPDATE {table} SET frozen = 1 WHERE frozen = 0 AND date({col}) <= ?" + _skip_closed(table, col),
                                    (upto,)).rowcount
        total = sum(out.values())
        if total:
            audit.note(con, "ledger", "freeze", f"Froze {total} entries dated up to {upto} "
                       + "(" + ", ".join(f"{KINDS[k][1].lower()} {n}" for k, n in out.items() if n) + ")")
        con.commit()
        return out
    except Exception:
        con.rollback()
        raise

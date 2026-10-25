"""Audit log: who-changed-what history that nobody can edit or delete.

Triggers inside the database write a row whenever important data is created, changed or deleted, so every
way of changing the data is covered automatically. The log table itself refuses UPDATE and DELETE."""
import json

# table -> (friendly name, columns to track, SQL for a readable label of the row (use {r} for old/new), events)
SPECS = {
    "products": ("Product", ["name", "sku", "category", "hsn_code", "barcode", "unit", "gst_percent", "reorder_level",
                             "active"], "{r}.name", "iud"),
    "batches": ("Batch", ["batch_no", "expiry_date", "cost_price", "mrp", "sale_price", "export_ready"],
                "(SELECT name FROM products WHERE id = {r}.product_id) || ' / ' || {r}.batch_no", "iud"),
    "parties": ("Customer / supplier", ["name", "phone", "email", "address", "gstin", "state_code", "price_list_id",
                                        "trade_discount", "credit_limit", "currency"], "{r}.name", "iud"),
    "sales": ("Bill", ["invoice_no", "customer_id", "subtotal", "discount", "tax", "total", "paid", "payment_method",
                       "balance_due", "frozen", "cancelled", "cancel_reason"], "{r}.invoice_no", "iud"),
    "purchases": ("Purchase", ["bill_no", "supplier_id", "purchase_date", "total", "paid", "balance_due", "frozen"],
                  "COALESCE(NULLIF({r}.bill_no, ''), '#' || {r}.id)", "iud"),
    "returns": ("Return", ["note_no", "kind", "ref_no", "party_id", "total", "refund_method", "reason", "frozen"],
                "{r}.note_no", "iud"),
    "payments": ("Payment", ["party_id", "amount", "mode", "note", "frozen"],
                 "COALESCE((SELECT name FROM parties WHERE id = {r}.party_id), '')", "iud"),
    "currencies": ("Currency", ["name", "symbol", "rate"], "{r}.code", "iud"),
    "expenses": ("Expense", ["spent_on", "category", "amount", "mode", "note", "frozen"], "{r}.category", "iud"),
    "price_lists": ("Price list", ["name", "basis", "adjust_percent"], "{r}.name", "iud"),
    "price_list_items": ("Price list", ["price"],
                         "(SELECT name FROM price_lists WHERE id = {r}.list_id) || ' / ' || "
                         "(SELECT name FROM products WHERE id = {r}.product_id)", "iud"),
    "schemes": ("Scheme", ["buy_qty", "free_qty", "active"],
                "(SELECT name FROM products WHERE id = {r}.product_id)", "iud"),
    "godowns": ("Godown", ["name"], "{r}.name", "iud"),
    "financial_years": ("Financial year", ["label", "start_date", "end_date", "status"], "{r}.label", "iud"),
    "pdcs": ("Cheque (PDC)", ["kind", "party_id", "cheque_no", "bank", "cheque_date", "amount", "status", "remarks"],
             "{r}.cheque_no || ' - ' || COALESCE((SELECT name FROM parties WHERE id = {r}.party_id), '')", "iud"),
}
KEY = {"price_list_items": "list_id", "settings": "key"}
SKIP_KEYS = "('alert_last_sent', 'alert_last_try', 'alert_last_result', 'pdc_alerted')"
SECRET_KEYS = "('smtp_pass')"
FRIENDLY = {t: v[0] for t, v in SPECS.items()}
FRIENDLY.update({"settings": "Setting", "stock_movements": "Stock"})

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    table_name TEXT NOT NULL,
    record_id  TEXT,
    action     TEXT NOT NULL,          -- create | update | delete | stock adjust
    old_value  TEXT,                   -- JSON of the old values (changed fields only for updates)
    new_value  TEXT,                   -- JSON of the new values
    note       TEXT                    -- readable name of the record
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_audit_table ON audit_log(table_name);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'The audit log cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'The audit log cannot be deleted'); END;
"""


def _obj(row, cols):
    return "json_object(" + ", ".join(f"'{c}', {row}.{c}" for c in cols) + ")"


def _changed(cols, pick):
    parts = [f"SELECT '{c}' k, {pick}.{c} v WHERE old.{c} IS NOT new.{c}" for c in cols]
    return "(SELECT json_group_object(k, v) FROM (" + " UNION ALL ".join(parts) + "))"


def _triggers():
    out = []
    for t, (_, cols, label, ev) in SPECS.items():
        key = KEY.get(t, "id")
        lab = lambda r: label.format(r=r)
        ins = lambda a, row, n, o, lb: (
            f"INSERT INTO audit_log(table_name, record_id, action, old_value, new_value, note) "
            f"VALUES('{t}', {row}.{key}, '{a}', {o}, {n}, {lb});")
        out.append(f"CREATE TRIGGER audit_{t}_ins AFTER INSERT ON {t} BEGIN "
                   + ins("create", "new", _obj("new", cols), "NULL", lab("new")) + " END;")
        out.append(f"CREATE TRIGGER audit_{t}_upd AFTER UPDATE ON {t} WHEN "
                   + " OR ".join(f"old.{c} IS NOT new.{c}" for c in cols) + " BEGIN "
                   + ins("update", "new", _changed(cols, "new"), _changed(cols, "old"), lab("new")) + " END;")
        out.append(f"CREATE TRIGGER audit_{t}_del AFTER DELETE ON {t} BEGIN "
                   + ins("delete", "old", "NULL", _obj("old", cols), lab("old")) + " END;")
    # settings (key/value): never log housekeeping keys, never show the email password
    shown = lambda r: f"CASE WHEN {r}.key IN {SECRET_KEYS} THEN '(hidden)' ELSE {r}.value END"
    out.append(f"CREATE TRIGGER audit_settings_ins AFTER INSERT ON settings WHEN new.key NOT IN {SKIP_KEYS} AND "
               f"new.value != '' BEGIN INSERT INTO audit_log(table_name, record_id, action, new_value, note) "
               f"VALUES('settings', new.key, 'create', json_object('value', {shown('new')}), new.key); END;")
    out.append(f"CREATE TRIGGER audit_settings_upd AFTER UPDATE ON settings WHEN new.key NOT IN {SKIP_KEYS} AND "
               f"old.value IS NOT new.value BEGIN INSERT INTO audit_log(table_name, record_id, action, old_value, new_value, note) "
               f"VALUES('settings', new.key, 'update', json_object('value', {shown('old')}), "
               f"json_object('value', {shown('new')}), new.key); END;")
    # manual stock corrections and expiry write-offs
    out.append("CREATE TRIGGER audit_stock_adj AFTER INSERT ON stock_movements WHEN new.reason IN ('adjustment', 'expired') "
               "BEGIN INSERT INTO audit_log(table_name, record_id, action, new_value, note) VALUES('stock_movements', "
               "new.batch_id, 'stock adjust', json_object('change', new.change_qty, 'reason', new.reason, 'note', new.note), "
               "(SELECT p.name || ' / ' || b.batch_no FROM batches b JOIN products p ON p.id = b.product_id "
               "WHERE b.id = new.batch_id)); END;")
    return out


def uninstall(con):
    """Drops the recording triggers (used only while loading demo data). The protection triggers stay."""
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'audit\\_%' ESCAPE '\\' "
                               "AND name NOT LIKE 'audit\\_log\\_%' ESCAPE '\\'").fetchall():
        con.execute(f"DROP TRIGGER {name}")


def install(con):
    """Creates the log table and (re)creates the recording triggers. Safe to run on every start."""
    con.executescript(TABLE_SQL)
    uninstall(con)
    for sql in _triggers():
        con.execute(sql)
    con.commit()


# ---------------------------------------------------------------- reading

LABEL = {"price_list_id": "price list", "hsn_code": "HSN", "gst_percent": "GST %", "gstin": "GSTIN",
         "state_code": "state", "balance_due": "balance due", "cost_price": "cost", "sale_price": "price",
         "export_ready": "export ready", "reorder_level": "reorder level", "trade_discount": "trade discount",
         "payment_method": "payment", "invoice_no": "invoice", "bill_no": "bill no.", "note_no": "note no.",
         "adjust_percent": "adjust %", "buy_qty": "buy", "free_qty": "free", "customer_id": "customer id",
         "supplier_id": "supplier id", "party_id": "party id", "cheque_no": "cheque no.", "cheque_date": "cheque date", "frozen": "frozen", "credit_limit": "credit limit", "cancel_reason": "cancel reason"}


def describe(js):
    """JSON from the log -> 'name: Soap, GST %: 18' for the screen."""
    if not js:
        return ""
    try:
        d = json.loads(js)
    except ValueError:
        return str(js)
    return ", ".join(f"{LABEL.get(k, k.replace('_', ' '))}: {'' if v is None else v}" for k, v in d.items())


def entries(con, table=None, action=None, d1=None, d2=None, text="", limit=2000):
    sql, args = "SELECT * FROM audit_log WHERE 1=1", []
    if table:
        sql += " AND table_name IN (%s)" % ",".join("?" * len(table))
        args += list(table)
    if action:
        sql += " AND action = ?"
        args.append(action)
    if d1:
        sql += " AND date(at) >= ?"
        args.append(d1)
    if d2:
        sql += " AND date(at) <= ?"
        args.append(d2)
    if text.strip():
        sql += " AND (IFNULL(note,'') LIKE ? OR IFNULL(old_value,'') LIKE ? OR IFNULL(new_value,'') LIKE ?)"
        args += [f"%{text.strip()}%"] * 3
    return con.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()


def note(con, table, action, text):
    """Adds a plain-English line to the log (for actions that touch many records at once). Does not commit."""
    con.execute("INSERT INTO audit_log(table_name, record_id, action, note) VALUES(?, NULL, ?, ?)", (table, action, text))

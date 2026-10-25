"""Post-dated cheques (PDC): received from customers, issued to suppliers.

Status flow: pending -> deposited -> cleared or bounced (a bounced cheque can be presented again).
Money is recorded only when a cheque CLEARS: that adds a payment and reduces the party's balance."""
from datetime import date

KINDS = {"received": "Received from customer", "issued": "Issued to supplier"}
STATUSES = ["pending", "deposited", "cleared", "bounced"]
NEXT = {"pending": ["deposited"], "deposited": ["cleared", "bounced"], "bounced": ["pending"], "cleared": []}
SQL = """SELECT d.*, p.name AS party,
                CAST(julianday(d.cheque_date) - julianday(date('now','localtime')) AS INTEGER) AS days_left
         FROM pdcs d JOIN parties p ON p.id = d.party_id"""


def validate(con, d, pid=None):
    """Returns a message when something is wrong, else None."""
    if not d.get("party_id"):
        return "Choose the customer or supplier."
    if not (d.get("cheque_no") or "").strip():
        return "Enter the cheque number."
    if (d.get("amount") or 0) <= 0:
        return "Enter the cheque amount."
    dup = con.execute("SELECT 1 FROM pdcs WHERE kind=? AND cheque_no=? AND bank=? AND id IS NOT ?",
                      (d["kind"], d["cheque_no"].strip(), (d.get("bank") or "").strip(), pid)).fetchone()
    if dup:
        return "This cheque number is already in the register for that bank."
    return None


def save(con, d, pid=None):
    problem = validate(con, d, pid)
    if problem:
        raise ValueError(problem)
    vals = (d["kind"], d["party_id"], d["cheque_no"].strip(), (d.get("bank") or "").strip(), d["cheque_date"],
            round(d["amount"], 2), (d.get("remarks") or "").strip() or None)
    if pid:
        row = con.execute("SELECT status FROM pdcs WHERE id=?", (pid,)).fetchone()
        if row["status"] != "pending":
            raise ValueError("Only a pending cheque can be edited.")
        con.execute("UPDATE pdcs SET kind=?, party_id=?, cheque_no=?, bank=?, cheque_date=?, amount=?, remarks=? WHERE id=?",
                    vals + (pid,))
    else:
        pid = con.execute("INSERT INTO pdcs(kind, party_id, cheque_no, bank, cheque_date, amount, remarks) "
                          "VALUES(?,?,?,?,?,?,?)", vals).lastrowid
    con.commit()
    return pid


def delete(con, pid):
    row = con.execute("SELECT status FROM pdcs WHERE id=?", (pid,)).fetchone()
    if row and row["status"] != "pending":
        raise ValueError("Only a pending cheque can be deleted. Cleared and bounced cheques stay as a record.")
    con.execute("DELETE FROM pdcs WHERE id=?", (pid,))
    con.commit()


def set_status(con, pid, new, note=""):
    """Moves a cheque along its flow. Clearing records the payment. Raises ValueError for a move that isn't allowed."""
    try:
        row = con.execute("SELECT * FROM pdcs WHERE id=?", (pid,)).fetchone()
        if not row:
            raise ValueError("Cheque not found.")
        if new not in NEXT[row["status"]]:
            raise ValueError(f"A {row['status']} cheque cannot be marked {new}.")
        pay_id = row["payment_id"]
        if new == "cleared":
            pay_id = con.execute("INSERT INTO payments(party_id, amount, mode, note) VALUES(?,?,?,?)",
                                 (row["party_id"], row["amount"], "cheque",
                                  f"Cheque {row['cheque_no']} {row['bank']} cleared")).lastrowid
            con.execute("UPDATE parties SET balance = balance - ? WHERE id = ?", (row["amount"], row["party_id"]))
        remarks = row["remarks"]
        if note:
            remarks = f"{remarks + ' | ' if remarks else ''}{new}: {note}"
        con.execute("UPDATE pdcs SET status=?, payment_id=?, remarks=?, status_at=datetime('now','localtime') WHERE id=?",
                    (new, pay_id, remarks, pid))
        con.commit()
    except Exception:
        con.rollback()
        raise


def listing(con, kind=None, status=None, search="", d1=None, d2=None):
    sql, args = SQL + " WHERE 1=1", []
    if kind:
        sql += " AND d.kind = ?"; args.append(kind)
    if status == "open":
        sql += " AND d.status IN ('pending','deposited')"
    elif status:
        sql += " AND d.status = ?"; args.append(status)
    if d1:
        sql += " AND d.cheque_date >= ?"; args.append(d1)
    if d2:
        sql += " AND d.cheque_date <= ?"; args.append(d2)
    if search.strip():
        sql += " AND (p.name LIKE ? OR d.cheque_no LIKE ? OR d.bank LIKE ?)"; args += [f"%{search.strip()}%"] * 3
    return con.execute(sql + " ORDER BY d.cheque_date, d.id", args).fetchall()


def maturing(con, days=7):
    """Pending / deposited cheques that are overdue or fall within the next `days` days."""
    return con.execute(SQL + " WHERE d.status IN ('pending','deposited') AND d.cheque_date <= date('now','localtime', ?) "
                       "ORDER BY d.cheque_date, d.id", (f"+{days} days",)).fetchall()


def due_now(con):
    """Pending cheques whose date has arrived (today or earlier)."""
    return con.execute(SQL + " WHERE d.status = 'pending' AND d.cheque_date <= date('now','localtime') "
                       "ORDER BY d.cheque_date", ()).fetchall()


def alert_text(con):
    """Text for the daily reminder, or None when nothing is due."""
    rows = due_now(con)
    if not rows:
        return None
    rec = [r for r in rows if r["kind"] == "received"]
    iss = [r for r in rows if r["kind"] == "issued"]
    lines = []
    if rec:
        lines.append(f"{len(rec)} cheque(s) received are ready to deposit (₹{sum(r['amount'] for r in rec):,.0f}):")
        lines += [f"   {r['party']}  -  cheque {r['cheque_no']}  -  ₹{r['amount']:,.0f}" for r in rec[:6]]
    if iss:
        lines.append(f"{len(iss)} cheque(s) you issued can be presented now (₹{sum(r['amount'] for r in iss):,.0f}):")
        lines += [f"   {r['party']}  -  cheque {r['cheque_no']}  -  ₹{r['amount']:,.0f}" for r in iss[:6]]
    return "\n".join(lines)

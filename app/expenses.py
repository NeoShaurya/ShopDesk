"""Shop expenses. They feed the profit & loss, the cash book and the bank book.
'GST paid to government' is not an expense: it settles the GST payable on the balance sheet instead."""
import sqlite3

GST_PAID = "GST paid to government"
CATEGORIES = ["Rent", "Salary & wages", "Electricity & water", "Transport & freight", "Repairs & maintenance", "Packing & stationery",
              "Telephone & internet", "Bank charges", "Insurance", "Other expenses", GST_PAID]
MODES = ["cash", "upi", "card", "cheque", "bank transfer"]


def add(con, spent_on, category, amount, mode="cash", note=""):
    from . import fy
    if (amount or 0) <= 0:
        raise ValueError("Enter the amount.")
    if not (category or "").strip():
        raise ValueError("Choose a category.")
    fy.guard(con, spent_on)
    try:
        eid = con.execute("INSERT INTO expenses(spent_on, category, amount, mode, note) VALUES(?,?,?,?,?)",
                          (spent_on, category.strip(), round(amount, 2), mode, (note or "").strip() or None)).lastrowid
        con.commit()
        return eid
    except sqlite3.DatabaseError as e:
        con.rollback()
        raise ValueError(str(e))


def delete(con, eid):
    try:
        con.execute("DELETE FROM expenses WHERE id = ?", (eid,))
        con.commit()
    except sqlite3.DatabaseError as e:           # frozen entry or closed year (raised by the database itself)
        con.rollback()
        raise ValueError(str(e))


def listing(con, d1=None, d2=None, category=None):
    sql, args = "SELECT * FROM expenses WHERE 1=1", []
    if d1:
        sql += " AND date(spent_on) >= ?"; args.append(d1)
    if d2:
        sql += " AND date(spent_on) <= ?"; args.append(d2)
    if category:
        sql += " AND category = ?"; args.append(category)
    return con.execute(sql + " ORDER BY spent_on DESC, id DESC", args).fetchall()

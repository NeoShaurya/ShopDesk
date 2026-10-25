"""Auto-saved drafts: one slot per screen (bill, purchase) holding whatever is half-entered."""
import json


def save(con, kind, data):
    con.execute("INSERT INTO drafts(kind, data, saved_at) VALUES(?, ?, datetime('now','localtime')) "
                "ON CONFLICT(kind) DO UPDATE SET data = excluded.data, saved_at = excluded.saved_at",
                (kind, json.dumps(data)))
    con.commit()


def load(con, kind):
    """(data, saved_at) or None."""
    r = con.execute("SELECT data, saved_at FROM drafts WHERE kind = ?", (kind,)).fetchone()
    return (json.loads(r["data"]), r["saved_at"]) if r else None


def clear(con, kind):
    con.execute("DELETE FROM drafts WHERE kind = ?", (kind,))
    con.commit()

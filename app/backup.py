"""One-click backup: a consistent copy of the database file."""
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(con, folder) -> Path:
    """Copies the live database into `folder` as shopdesk_backup_YYYY-MM-DD_HHMM.db and
    verifies the copy. Uses SQLite's own backup, so it is safe while the app is open."""
    folder = Path(folder)
    stamp = f"{datetime.now():%Y-%m-%d_%H%M}"
    dest = folder / f"shopdesk_backup_{stamp}.db"
    n = 2
    while dest.exists():                       # two backups in the same minute: never overwrite
        dest = folder / f"shopdesk_backup_{stamp}_{n}.db"
        n += 1
    out = sqlite3.connect(dest)
    try:
        con.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("The backup copy failed its integrity check.")
    except Exception:
        out.close()
        dest.unlink(missing_ok=True)
        raise
    out.close()
    return dest

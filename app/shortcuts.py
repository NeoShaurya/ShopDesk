"""Keyboard shortcuts: the list of actions, their default keys, and the user's own choices (saved in settings)."""
from . import db

# id, label (shown only in Settings), default key
ACTIONS = [
    ("search", "Search everywhere", "Ctrl+K"),
    ("go_home", "Open Home", "Ctrl+H"),
    ("go_billing", "Open Billing", "Ctrl+B"),
    ("go_purchases", "Open Purchases", "Ctrl+U"),
    ("go_inventory", "Open Inventory", "Ctrl+I"),
    ("go_parties", "Open Customers & Suppliers", "Ctrl+Shift+C"),
    ("go_reports", "Open Reports", "Ctrl+R"),
    ("save_bill", "Save the bill", "F9"),
    ("save_print_bill", "Save and print the bill", "F10"),
    ("backup", "Backup database", "Ctrl+Shift+B"),
    ("dark_mode", "Switch dark mode on / off", "Ctrl+D"),
]
DEFAULTS = {a: k for a, _, k in ACTIONS}


def current(con):
    """{action id: key text}. An empty string means the action has no shortcut."""
    s = db.get_settings(con)
    return {a: s.get(f"sc_{a}", d) for a, _, d in ACTIONS}


def save(con, mapping):
    """Raises ValueError if two actions would share a key."""
    seen = {}
    for a, key in mapping.items():
        if key and key in seen:
            labels = {i: l for i, l, _ in ACTIONS}
            raise ValueError(f"{key} is used for both '{labels[seen[key]]}' and '{labels[a]}'. Give each one its own key.")
        seen[key] = a
    db.save_settings(con, {f"sc_{a}": key for a, key in mapping.items()})

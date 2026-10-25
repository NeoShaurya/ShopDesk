import re

_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def clean_gstin(s: str) -> str:
    return (s or "").replace(" ", "").upper()


def valid_gstin(s: str) -> bool:
    return bool(_GSTIN.match(clean_gstin(s)))


def valid_hsn(s: str) -> bool:
    """HSN codes are 4, 6 or 8 digits (blank is allowed elsewhere)."""
    return bool(re.fullmatch(r"\d{4}|\d{6}|\d{8}", (s or "").strip()))


DEFAULT_RATES = (0.0, 5.0, 12.0, 18.0, 28.0)


def parse_rates(text):
    """'0, 5, 18, 40' -> [0.0, 5.0, 18.0, 40.0]. Raises ValueError with a plain message when it can't be used."""
    try:
        vals = sorted({round(float(x), 2) for x in str(text).replace(";", ",").split(",") if x.strip()})
    except ValueError:
        raise ValueError("Type the rates as numbers separated by commas, for example: 0, 5, 18, 40")
    if not vals:
        raise ValueError("Enter at least one GST rate.")
    if vals[0] < 0 or vals[-1] > 100:
        raise ValueError("A GST rate must be between 0 and 100.")
    if 0.0 not in vals:
        raise ValueError("Keep 0 in the list (nil-rated and exempt goods).")
    if len(vals) > 10:
        raise ValueError("That is too many rates. Keep only the ones you really use.")
    return vals


def rates(con):
    """The GST rates this shop allows on products (Settings > GST rates)."""
    from . import db
    try:
        return parse_rates(db.get_settings(con)["gst_rates"])
    except ValueError:
        return list(DEFAULT_RATES)


def fmt_rates(vals):
    return ", ".join(f"{v:g}" for v in vals)


def check_product(d, allowed=None):
    """Returns a message for the first problem found in a product's details, or None when they are fine."""
    if not (d.get("name") or "").strip():
        return "Please enter a product name."
    rate = float(d.get("gst_percent") or 0)
    allowed = list(allowed if allowed is not None else DEFAULT_RATES)
    if rate not in allowed:
        return "GST rate must be one of " + ", ".join(f"{r:g}%" for r in allowed) + "."
    hsn = (d.get("hsn_code") or "").strip()
    if hsn and not valid_hsn(hsn):
        return "HSN code must be 4, 6 or 8 digits (or leave it blank)."
    return None

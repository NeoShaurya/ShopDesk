"""GST reports built from bills, purchases and credit/debit notes:
GSTR-1 (outward supplies) and GSTR-3B (monthly summary). These are SUMMARIES to help you or your CA
prepare the return - always check them before filing."""
import calendar
import csv
import re
from datetime import datetime
from pathlib import Path

from . import db
from .gst import clean_gstin, valid_gstin, fmt_rates as gst_fmt, rates as gst_rates
from .reports import fmt

STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}
UQC = {"pcs": "PCS-PIECES", "pc": "PCS-PIECES", "nos": "NOS-NUMBERS", "kg": "KGS-KILOGRAMS", "kgs": "KGS-KILOGRAMS",
       "g": "GMS-GRAMMES", "gm": "GMS-GRAMMES", "ltr": "LTR-LITRES", "l": "LTR-LITRES", "ml": "MLT-MILLILITRE",
       "box": "BOX-BOX", "pack": "PAC-PACKS", "pkt": "PAC-PACKS", "dozen": "DOZ-DOZENS", "bag": "BAG-BAGS",
       "bottle": "BTL-BOTTLES", "mtr": "MTR-METERS", "m": "MTR-METERS", "set": "SET-SETS", "pair": "PRS-PAIRS"}


def month_bounds(year, month):
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def pos_label(code):
    return f"{code}-{STATES.get(code, '')}" if code else ""


def state_code(gstin, explicit):
    """State of a party: the one chosen on their record, else the first two digits of a valid GSTIN."""
    if explicit:
        return explicit
    g = clean_gstin(gstin)
    return g[:2] if valid_gstin(g) else None


def shop_state(con):
    g = clean_gstin(db.get_settings(con)["shop_gstin"])
    return g[:2] if valid_gstin(g) else None


def split_tax(tax, inter):
    """(IGST, CGST, SGST): inter-state supplies pay IGST, local ones pay half CGST + half SGST."""
    if inter:
        return round(tax, 2), 0.0, 0.0
    c = round(tax / 2, 2)
    return 0.0, c, round(tax - c, 2)


def setoff(liab, itc):
    """GST rules for paying tax with input credit. liab / itc = {'i','c','s'}.
    IGST credit pays IGST, then CGST, then SGST; CGST credit pays CGST then IGST; SGST credit pays SGST then IGST.
    Returns (used[credit][head], cash payable per head, credit left per head)."""
    liab, itc = dict(liab), dict(itc)
    used = {k: {"i": 0.0, "c": 0.0, "s": 0.0} for k in "ics"}
    for credit, order in (("i", "ics"), ("c", "ci"), ("s", "si")):
        for head in order:
            x = round(min(itc[credit], liab[head]), 2)
            used[credit][head] = x
            itc[credit] = round(itc[credit] - x, 2)
            liab[head] = round(liab[head] - x, 2)
    return used, liab, itc


# ------------------------------------------------------------------ data

def collect(con, year, month):
    return collect_range(con, *month_bounds(year, month))


def collect_range(con, d1, d2):
    home = shop_state(con)

    def party(gstin, explicit):
        reg = valid_gstin(gstin or "")
        st = state_code(gstin, explicit) or home
        return reg, st, bool(home and st and st != home)

    sales = []
    for r in con.execute(
            """SELECT s.id AS sale_id, s.invoice_no, s.sale_date, s.total AS inv_total, s.subtotal, s.tax, s.discount,
                      COALESCE(c.name, 'Walk-in Customer') AS cust, c.gstin, c.state_code, si.qty, si.line_total,
                      si.gst_percent, p.hsn_code, p.name AS pname, p.unit
               FROM sale_items si JOIN sales s ON s.id = si.sale_id JOIN products p ON p.id = si.product_id
               LEFT JOIN parties c ON c.id = s.customer_id
               WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ? ORDER BY s.id, si.id""", (d1, d2)):
        gross = (r["subtotal"] or 0) + (r["tax"] or 0)
        ratio = (r["discount"] or 0) / gross if gross else 0.0       # invoice-level discount, spread over the lines
        taxable = round(r["line_total"] * (1 - ratio), 2)
        tax = round(taxable * r["gst_percent"] / 100, 2)
        reg, st, inter = party(r["gstin"], r["state_code"])
        sales.append({**dict(r), "registered": reg, "pos": st, "inter": inter, "rate": r["gst_percent"],
                      "taxable": taxable, "tax": tax, "split": split_tax(tax, inter)})

    credits = []
    for r in con.execute(
            """SELECT r.note_no, r.created_at, r.ref_no, r.total AS note_total, COALESCE(c.name, 'Walk-in Customer') AS cust,
                      c.gstin, c.state_code, ri.qty, ri.taxable_value, ri.gst_amount, ri.gst_percent, ri.hsn_code
               FROM return_items ri JOIN returns r ON r.id = ri.return_id AND r.kind = 'customer'
               LEFT JOIN parties c ON c.id = r.party_id
               WHERE date(r.created_at) BETWEEN ? AND ? ORDER BY r.id, ri.id""", (d1, d2)):
        reg, st, inter = party(r["gstin"], r["state_code"])
        credits.append({**dict(r), "registered": reg, "pos": st, "inter": inter, "rate": r["gst_percent"],
                        "taxable": r["taxable_value"], "tax": r["gst_amount"], "split": split_tax(r["gst_amount"], inter)})

    purchases = []
    for r in con.execute(
            """SELECT pu.id, COALESCE(NULLIF(pu.bill_no,''), '#' || pu.id) AS inv, s.name AS supplier, s.gstin, s.state_code,
                      pi.taxable_value, pi.gst_amount, pi.gst_percent
               FROM purchase_items pi JOIN purchases pu ON pu.id = pi.purchase_id LEFT JOIN parties s ON s.id = pu.supplier_id
               WHERE date(pu.purchase_date) BETWEEN ? AND ?""", (d1, d2)):
        reg, st, inter = party(r["gstin"], r["state_code"])
        purchases.append({**dict(r), "registered": reg, "inter": inter, "rate": r["gst_percent"],
                          "taxable": r["taxable_value"], "tax": r["gst_amount"], "split": split_tax(r["gst_amount"], inter)})

    debits = []
    for r in con.execute(
            """SELECT r.note_no, s.name AS supplier, s.gstin, s.state_code, ri.taxable_value, ri.gst_amount, ri.gst_percent
               FROM return_items ri JOIN returns r ON r.id = ri.return_id AND r.kind = 'supplier'
               LEFT JOIN parties s ON s.id = r.party_id
               WHERE date(r.created_at) BETWEEN ? AND ?""", (d1, d2)):
        reg, st, inter = party(r["gstin"], r["state_code"])
        debits.append({**dict(r), "registered": reg, "inter": inter, "rate": r["gst_percent"],
                       "taxable": r["taxable_value"], "tax": r["gst_amount"], "split": split_tax(r["gst_amount"], inter)})

    docs = {}
    for label, sql in (("inv", "SELECT invoice_no FROM sales WHERE date(sale_date) BETWEEN ? AND ?"),
                       ("inv_cancelled", "SELECT invoice_no FROM sales WHERE cancelled = 1 AND date(sale_date) BETWEEN ? AND ?"),
                       ("cn", "SELECT note_no FROM returns WHERE kind='customer' AND date(created_at) BETWEEN ? AND ?")):
        docs[label] = [r[0] for r in con.execute(sql, (d1, d2))]
    bad_gstin = [r["name"] for r in con.execute("SELECT name, gstin FROM parties WHERE IFNULL(gstin,'') != ''")
                 if not valid_gstin(r["gstin"])]
    return {"d1": d1, "d2": d2, "home": home, "sales": sales, "credits": credits, "purchases": purchases,
            "debits": debits, "docs": docs, "bad_gstin": bad_gstin, "allowed_rates": gst_rates(con)}


def _period(year, month):
    return f"{calendar.month_name[month]} {year}"


def _sum(rows, key):
    return round(sum(r[key] for r in rows), 2)


def _heads(rows):
    return [round(sum(r["split"][i] for r in rows), 2) for i in range(3)]


def _warnings(data):
    w = []
    if not data["home"]:
        w.append("Your shop GSTIN is not set (or is not valid) in Settings, so IGST cannot be separated from "
                 "CGST/SGST. All tax is shown as CGST + SGST.")
    no_hsn = sorted({r["pname"] for r in data["sales"] if not (r["hsn_code"] or "").strip() and r["taxable"] > 0})
    if no_hsn:
        w.append(f"{len(no_hsn)} product(s) sold this month have no HSN code: " + ", ".join(no_hsn[:6]) +
                 (" ..." if len(no_hsn) > 6 else "") + ". Add them in Inventory.")
    odd = sorted({r["rate"] for r in data["sales"] + data["purchases"] if r["rate"] not in data["allowed_rates"]})
    if odd:
        w.append("Some items use a GST rate that is not in your allowed list (" + gst_fmt(data["allowed_rates"]) + "): "
                 + ", ".join(f"{x:g}%" for x in odd) + ". This is normal for old months before a rate change.")
    if data["bad_gstin"]:
        w.append("These parties have a GSTIN that does not look valid, so they are treated as unregistered: " +
                 ", ".join(data["bad_gstin"][:5]) + ".")
    if not data["sales"]:
        w.append("There are no bills in this month.")
    return w


def _prefix(number):
    return re.sub(r"\d+$", "", number)


def _doc_range(numbers, prefix):
    nums = sorted(int(n.split("-")[-1]) for n in numbers if n.split("-")[-1].isdigit())
    if not nums:
        return "-", "-", 0, 0
    width = len(numbers[0].split("-")[-1])
    return (f"{prefix}{nums[0]:0{width}d}", f"{prefix}{nums[-1]:0{width}d}", len(nums), (nums[-1] - nums[0] + 1) - len(nums))


def _plain(rows):
    """Display strings -> plain values for CSV (no thousands separators in numbers)."""
    out = []
    for r in rows:
        line = []
        for c in r:
            s = str(c).replace(",", "")
            try:
                float(s)
                line.append(s)
            except ValueError:
                line.append(c)
        out.append(line)
    return out


def _sec(heading, headers, aligns, rows, footer=None, note="", sheet="", csv_headers=None, csv_rows=None):
    return {"heading": heading, "headers": headers, "aligns": aligns, "rows": rows, "footer": footer or [], "note": note,
            "sheet": sheet or heading[:28], "csv_headers": csv_headers, "csv_rows": csv_rows}


# ------------------------------------------------------------------ GSTR-1

def gstr1(con, year, month):
    data = collect(con, year, month)
    sales, credits = data["sales"], data["credits"]
    f2 = lambda v: f"{v:.2f}"

    # ---- B2B: registered customers, one row per invoice and tax rate
    groups = {}
    for r in (x for x in sales if x["registered"] and x["taxable"] + x["tax"] > 0):
        g = groups.setdefault((r["invoice_no"], r["rate"]), {**r, "t": 0.0, "h": [0.0, 0.0, 0.0]})
        g["t"] += r["taxable"]
        g["h"] = [g["h"][i] + r["split"][i] for i in range(3)]
    keys = sorted(groups, key=lambda k: (groups[k]["sale_id"], k[1]))
    dt = lambda s, f: datetime.fromisoformat(s).strftime(f)
    b2b = [[groups[k]["gstin"], groups[k]["cust"], k[0], dt(groups[k]["sale_date"], "%d %b %Y"),
            fmt(groups[k]["inv_total"]), f"{k[1]:g}%", fmt(groups[k]["t"]), *[fmt(x) for x in groups[k]["h"]]] for k in keys]
    b2b_csv = [[groups[k]["gstin"], groups[k]["cust"], k[0], dt(groups[k]["sale_date"], "%d-%b-%y"),
                f2(groups[k]["inv_total"]), pos_label(groups[k]["pos"]), "N", "", "Regular", "", f"{k[1]:g}",
                f2(groups[k]["t"]), "0.00"] for k in keys]
    gs = [groups[k] for k in keys]
    b2b_foot = [["", "", "", "Total", "", "", fmt(sum(g["t"] for g in gs)), *[fmt(sum(g["h"][i] for g in gs)) for i in range(3)]]]

    # ---- B2C: unregistered customers by place of supply and rate, net of their credit notes
    agg = {}
    for r in sales:
        if not r["registered"]:
            a = agg.setdefault((r["pos"] or "", r["rate"]), [0.0, 0.0, 0.0, 0.0])
            a[0] += r["taxable"]
            for i in range(3):
                a[i + 1] += r["split"][i]
    for c in (x for x in credits if not x["registered"]):
        a = agg.setdefault((c["pos"] or "", c["rate"]), [0.0, 0.0, 0.0, 0.0])
        a[0] -= c["taxable"]
        for i in range(3):
            a[i + 1] -= c["split"][i]
    ks = sorted(k for k in agg if any(abs(v) > 0.004 for v in agg[k]))
    b2c = [[pos_label(k[0]) or "-", f"{k[1]:g}%", *[fmt(v) for v in agg[k]]] for k in ks]
    b2c_csv = [["OE", pos_label(k[0]), "", f"{k[1]:g}", f2(agg[k][0]), "0.00", ""] for k in ks]
    b2c_foot = [["Total", "", *[fmt(sum(agg[k][i] for k in ks)) for i in range(4)]]]

    # ---- credit notes to registered customers
    cg = {}
    for c in (x for x in credits if x["registered"]):
        g = cg.setdefault((c["note_no"], c["rate"]), {**c, "t": 0.0, "h": [0.0, 0.0, 0.0]})
        g["t"] += c["taxable"]
        g["h"] = [g["h"][i] + c["split"][i] for i in range(3)]
    ck = sorted(cg)
    cdnr = [[cg[k]["gstin"], cg[k]["cust"], k[0], dt(cg[k]["created_at"], "%d %b %Y"), cg[k]["ref_no"] or "",
             f"{k[1]:g}%", fmt(cg[k]["t"]), *[fmt(x) for x in cg[k]["h"]]] for k in ck]
    cdnr_csv = [[cg[k]["gstin"], cg[k]["cust"], k[0], dt(cg[k]["created_at"], "%d-%b-%y"), "C", pos_label(cg[k]["pos"]),
                 "N", "Regular", f2(cg[k]["note_total"]), "", f"{k[1]:g}", f2(cg[k]["t"]), "0.00"] for k in ck]

    # ---- HSN summary
    hs = {}
    for r in sales:
        h = (r["hsn_code"] or "").strip() or "(missing)"
        a = hs.setdefault((h, r["rate"]), {"desc": r["pname"], "uqc": UQC.get((r["unit"] or "").strip().lower(), "OTH-OTHERS"),
                                           "qty": 0, "taxable": 0.0, "h": [0.0, 0.0, 0.0]})
        a["qty"] += r["qty"]
        a["taxable"] += r["taxable"]
        a["h"] = [a["h"][i] + r["split"][i] for i in range(3)]
    hk = sorted(hs)
    hsn = [[k[0], hs[k]["desc"], hs[k]["uqc"], hs[k]["qty"], f"{k[1]:g}%", fmt(hs[k]["taxable"] + sum(hs[k]["h"])),
            fmt(hs[k]["taxable"]), *[fmt(x) for x in hs[k]["h"]]] for k in hk]
    hsn_csv = [[k[0], hs[k]["desc"], hs[k]["uqc"], hs[k]["qty"], f"{k[1]:g}", f2(hs[k]["taxable"] + sum(hs[k]["h"])),
                f2(hs[k]["taxable"]), *[f2(x) for x in hs[k]["h"]], "0.00"] for k in hk]
    hsn_foot = [["Total", "", "", sum(hs[k]["qty"] for k in hk), "", fmt(sum(hs[k]["taxable"] + sum(hs[k]["h"]) for k in hk)),
                 fmt(sum(hs[k]["taxable"] for k in hk)), *[fmt(sum(hs[k]["h"][i] for k in hk)) for i in range(3)]]]

    # ---- documents issued
    docs = []                                         # one row per invoice series (RET-, WHO-, INV- ...)
    series = sorted({_prefix(x) for x in data["docs"]["inv"]})
    for pre in series or ["INV-"]:
        mine = [x for x in data["docs"]["inv"] if _prefix(x) == pre]
        a, b, n, canc = _doc_range(mine, pre)
        canc += sum(1 for x in data["docs"]["inv_cancelled"] if _prefix(x) == pre)
        docs.append(["Invoices for outward supply", a, b, n, canc])
    c1, c2, cn, ccanc = _doc_range(data["docs"]["cn"], "CN-")
    docs.append(["Credit notes", c1, c2, cn, ccanc])

    sections = [
        _sec("B2B invoices (customers with a GSTIN)", ["GSTIN", "Customer", "Invoice no.", "Date", "Invoice value", "Rate",
             "Taxable", "IGST", "CGST", "SGST"], "lllllrrrrr", b2b, b2b_foot, sheet="B2B",
             csv_headers=["GSTIN/UIN of Recipient", "Receiver Name", "Invoice Number", "Invoice date", "Invoice Value",
                          "Place Of Supply", "Reverse Charge", "Applicable % of Tax Rate", "Invoice Type",
                          "E-Commerce GSTIN", "Rate", "Taxable Value", "Cess Amount"], csv_rows=b2b_csv),
        _sec("B2C (customers without a GSTIN), by rate", ["Place of supply", "Rate", "Taxable", "IGST", "CGST", "SGST"],
             "llrrrr", b2c, b2c_foot, note="Net of credit notes given to these customers.", sheet="B2C",
             csv_headers=["Type", "Place Of Supply", "Applicable % of Tax Rate", "Rate", "Taxable Value", "Cess Amount",
                          "E-Commerce GSTIN"], csv_rows=b2c_csv),
        _sec("Credit notes to customers with a GSTIN", ["GSTIN", "Customer", "Note no.", "Date", "Against invoice", "Rate",
             "Taxable", "IGST", "CGST", "SGST"], "lllllrrrrr", cdnr, sheet="Credit notes",
             csv_headers=["GSTIN/UIN of Recipient", "Receiver Name", "Note Number", "Note Date", "Note Type",
                          "Place Of Supply", "Reverse Charge", "Note Supply Type", "Note Value",
                          "Applicable % of Tax Rate", "Rate", "Taxable Value", "Cess Amount"], csv_rows=cdnr_csv),
        _sec("HSN-wise summary of outward supplies", ["HSN", "Description", "UQC", "Qty", "Rate", "Total value", "Taxable",
             "IGST", "CGST", "SGST"], "lllrrrrrrr", hsn, hsn_foot, note="Invoiced goods; returns are in the credit notes table.",
             sheet="HSN", csv_headers=["HSN", "Description", "UQC", "Total Quantity", "Rate", "Total Value", "Taxable Value",
                                       "Integrated Tax Amount", "Central Tax Amount", "State/UT Tax Amount", "Cess Amount"],
             csv_rows=hsn_csv),
        _sec("Documents issued", ["Nature of document", "From", "To", "Total number", "Cancelled / missing"], "lllrr", docs,
             sheet="Documents", csv_headers=["Nature of Document", "Sr. No. From", "Sr. No. To", "Total Number", "Cancelled"],
             csv_rows=[list(r) for r in docs]),
    ]
    notes = ["Free (scheme) goods carry no taxable value here. Ask your CA whether they should be treated as taxable supplies.",
             "Customers without a GSTIN are taken to be in your state unless you set their State in Customers & Suppliers.",
             "The CSV files follow the layout of the GST offline-tool templates. Compare them with the current template before uploading.",
             "This is a summary to help prepare the return. Check it with your CA before filing."]
    return {"kind": "gstr1", "title": f"GSTR-1 Summary - {_period(year, month)}",
            "subtitle": f"Outward supplies {data['d1']} to {data['d2']}  |  {len(set(r['invoice_no'] for r in sales))} invoices",
            "sections": sections, "warnings": _warnings(data), "notes": notes, "prefix": f"GSTR1_{year}-{month:02d}"}


# ------------------------------------------------------------------ GSTR-3B

def gstr3b(con, year, month):
    data = collect(con, year, month)
    sales, credits, purchases, debits = data["sales"], data["credits"], data["purchases"], data["debits"]

    def net(rows_s, rows_c):
        t = round(_sum(rows_s, "taxable") - _sum(rows_c, "taxable"), 2)
        return t, [round(a - b, 2) for a, b in zip(_heads(rows_s), _heads(rows_c))]

    ta, ha = net([r for r in sales if r["rate"] > 0], [r for r in credits if r["rate"] > 0])
    tn, _ = net([r for r in sales if r["rate"] == 0], [r for r in credits if r["rate"] == 0])
    z = fmt(0)
    s31 = _sec("3.1  Outward supplies (net of credit notes)", ["Nature of supplies", "Taxable value", "IGST", "CGST", "SGST", "Cess"], "lrrrrr", [
        ["(a) Taxable supplies (other than zero rated, nil rated, exempt)", fmt(ta), *[fmt(x) for x in ha], z],
        ["(b) Zero rated supplies", z, z, z, z, z],
        ["(c) Nil rated / exempt supplies", fmt(tn), z, z, z, z],
        ["(d) Inward supplies liable to reverse charge", z, z, z, z, z],
        ["(e) Non-GST outward supplies", z, z, z, z, z]], sheet="3.1")

    un_s = [r for r in sales if not r["registered"] and r["inter"] and r["rate"] > 0]
    un_c = [r for r in credits if not r["registered"] and r["inter"] and r["rate"] > 0]
    rows32 = []
    for st in sorted({r["pos"] for r in un_s + un_c}):
        t, h = net([r for r in un_s if r["pos"] == st], [r for r in un_c if r["pos"] == st])
        rows32.append([pos_label(st), fmt(t), fmt(h[0])])
    s32 = _sec("3.2  Inter-state supplies to customers without a GSTIN, by state", ["Place of supply", "Taxable value", "IGST"],
               "lrr", rows32, note="" if rows32 else "No inter-state sales to unregistered customers. Set a customer's State "
               "(Customers & Suppliers > Edit) to record sales to another state.", sheet="3.2")

    itc_p = [r for r in purchases if r["registered"] and r["rate"] > 0]
    itc_d = [r for r in debits if r["registered"] and r["rate"] > 0]
    inel = [r for r in purchases if not r["registered"] and r["rate"] > 0]
    ha5, hb2 = _heads(itc_p), _heads(itc_d)
    net_itc = [round(a - b, 2) for a, b in zip(ha5, hb2)]
    s4 = _sec("4.  Eligible input tax credit (ITC)", ["Details", "IGST", "CGST", "SGST", "Cess"], "lrrrr", [
        ["(A)(5) All other ITC - purchases from suppliers with a GSTIN", *[fmt(x) for x in ha5], z],
        ["(B)(2) ITC reversed - purchase returns (debit notes)", *[fmt(x) for x in hb2], z],
        ["(C) Net ITC available (A - B)", *[fmt(x) for x in net_itc], z],
        ["(D) Not claimable - tax on purchases from suppliers without a valid GSTIN", *[fmt(x) for x in _heads(inel)], z]],
        sheet="4", note="Tax on purchases from suppliers without a valid GSTIN cannot be claimed as credit.")

    zero_p = [r for r in purchases if r["rate"] == 0]
    s5 = _sec("5.  Exempt, nil-rated and non-GST inward supplies", ["Details", "Inter-state", "Intra-state"], "lrr", [
        ["From suppliers under composition scheme, exempt and nil rated supplies",
         fmt(_sum([r for r in zero_p if r["inter"]], "taxable")), fmt(_sum([r for r in zero_p if not r["inter"]], "taxable"))],
        ["Non-GST supply", z, z]], sheet="5")

    liab = {"i": max(ha[0], 0), "c": max(ha[1], 0), "s": max(ha[2], 0)}
    itc = {"i": max(net_itc[0], 0), "c": max(net_itc[1], 0), "s": max(net_itc[2], 0)}
    used, cash, left = setoff(liab, itc)
    s61 = _sec("6.1  Payment of tax", ["Details", "IGST", "CGST", "SGST"], "lrrr", [
        ["Tax payable (from 3.1)", fmt(liab["i"]), fmt(liab["c"]), fmt(liab["s"])],
        ["Paid using IGST credit", fmt(used["i"]["i"]), fmt(used["i"]["c"]), fmt(used["i"]["s"])],
        ["Paid using CGST credit", fmt(used["c"]["i"]), fmt(used["c"]["c"]), z],
        ["Paid using SGST credit", fmt(used["s"]["i"]), z, fmt(used["s"]["s"])],
        ["TAX TO PAY IN CASH", fmt(cash["i"]), fmt(cash["c"]), fmt(cash["s"])],
        ["Credit left to carry forward", fmt(left["i"]), fmt(left["c"]), fmt(left["s"])]], sheet="6.1",
        note="Credit is used in the order the GST rules require: IGST credit first, then CGST and SGST credit.")
    notes = ["Taxable supplies are net of credit notes issued in the month; ITC is net of debit notes.",
             "Customers and suppliers without a state are taken to be in your state, so their tax is CGST + SGST.",
             "This is a summary to help prepare the return. Check it with your CA before filing."]
    return {"kind": "gstr3b", "title": f"GSTR-3B Summary - {_period(year, month)}",
            "subtitle": f"Monthly return {data['d1']} to {data['d2']}", "sections": [s31, s32, s4, s5, s61],
            "warnings": _warnings(data), "notes": notes, "prefix": f"GSTR3B_{year}-{month:02d}"}


# ------------------------------------------------------------------ export helpers

def sections_to_reps(rep):
    """One report dict per section (used for Excel: one sheet each)."""
    return [{"title": s["heading"], "subtitle": rep["title"], "sheet": s["sheet"], "headers": s["headers"],
             "aligns": s["aligns"], "rows": s["rows"], "footer": s["footer"]} for s in rep["sections"]]


def write_csvs(rep, folder):
    """One CSV per section (portal-style layout where one exists). Returns the file paths."""
    folder, paths = Path(folder), []
    for s in rep["sections"]:
        slug = "".join(ch if ch.isalnum() else "_" for ch in s["sheet"]).strip("_").lower()
        path = folder / f"{rep['prefix']}_{slug}.csv"
        headers = s["csv_headers"] or s["headers"]
        rows = s["csv_rows"] if s["csv_rows"] is not None else _plain(s["rows"] + s["footer"])
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(rows)
        paths.append(path)
    return paths

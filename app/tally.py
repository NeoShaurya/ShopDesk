"""Export to Tally: vouchers (sales, purchases, credit / debit notes, receipts, payments) as Tally import XML.

Two files are written: the ledgers they use (import this first) and the vouchers. Debits are negative amounts
and credits positive, which is how Tally's own XML works, so every voucher must add up to exactly zero."""
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from . import db
from .gst import clean_gstin, valid_gstin
from .gstr import STATES, split_tax, state_code

DEFAULT_OPTS = {"company": "", "sales": "Sales", "purchase": "Purchase", "cash": "Cash", "bank": "Bank Account"}
DUTY = {"i": ("IGST", "Integrated Tax"), "c": ("CGST", "Central Tax"), "s": ("SGST", "State Tax")}
ROUND_OFF = "Round Off"


def load_opts(con):
    s = db.get_settings(con)
    o = {k: s.get(f"tally_{k}", "") or DEFAULT_OPTS[k] for k in DEFAULT_OPTS}
    o["company"] = s.get("tally_company") or s["shop_name"]
    return o


def save_opts(con, o):
    db.save_settings(con, {f"tally_{k}": v for k, v in o.items()})


def _amt(x):
    return f"{x:.2f}"


def _date(s):
    return datetime.fromisoformat(s).strftime("%Y%m%d")


def _entry(name, signed, party=False):
    """One ledger line. Debit = negative amount."""
    return {"name": name, "amount": round(signed, 2), "party": party}


class Builder:
    def __init__(self, con, opts, d1, d2):
        self.con, self.o, self.d1, self.d2 = con, opts, d1, d2
        s = db.get_settings(con)
        g = clean_gstin(s["shop_gstin"])
        self.shop_gstin = g if valid_gstin(g) else ""
        self.home = g[:2] if self.shop_gstin else None
        self.vouchers, self.parties, self.counts, self.warnings = [], {}, {}, []
        self.used_duty = set()

    # ---- helpers
    def party(self, name, ptype, gstin, explicit_state):
        name = name or "Walk-in Customer"
        reg = valid_gstin(gstin or "")
        st = state_code(gstin, explicit_state) or self.home
        self.parties.setdefault(name, {"type": ptype, "gstin": clean_gstin(gstin) if reg else "", "state": st})
        return reg, st, bool(self.home and st and st != self.home)

    def duty_entries(self, tax, inter, prefix, sign):
        """GST ledger lines for `tax`. sign=+1 credit, -1 debit."""
        out = []
        for head, amount in zip("ics", split_tax(tax, inter)):
            if amount:
                self.used_duty.add((prefix, head))
                out.append(_entry(f"{prefix} {DUTY[head][0]}", sign * amount))
        return out

    def add(self, vtype, number, date, party, entries, narration="", reference="", pgstin="", pos=None):
        total = round(sum(e["amount"] for e in entries), 2)
        if abs(total) > 0.005:
            raise ValueError(f"{vtype} {number} does not balance ({total:+.2f}); nothing was exported.")
        if not entries:
            return
        self.vouchers.append({"type": vtype, "number": number, "date": date, "party": party, "entries": entries,
                              "narration": narration, "reference": reference, "gstin": pgstin, "pos": pos})
        self.counts[vtype] = self.counts.get(vtype, 0) + 1

    def money_ledger(self, method):
        m = (method or "cash").lower()
        return self.o["cash"] if m.startswith("cash") or m == "credit" else self.o["bank"]

    # ---- documents
    def run(self):
        c, o = self.con, self.o
        # sales
        for r in c.execute(
                """SELECT s.*, COALESCE(p.name,'Walk-in Customer') AS pname, p.gstin, p.state_code
                   FROM sales s LEFT JOIN parties p ON p.id = s.customer_id
                   WHERE s.cancelled = 0 AND date(s.sale_date) BETWEEN ? AND ? ORDER BY s.id""", (self.d1, self.d2)):
            reg, st, inter = self.party(r["pname"], "customer", r["gstin"], r["state_code"])
            gross = r["subtotal"] + r["tax"]
            k = 1 - (r["discount"] / gross if gross else 0)
            taxable, tax = round(r["subtotal"] * k, 2), round(r["tax"] * k, 2)
            es = [_entry(r["pname"], -r["total"], True), _entry(o["sales"], taxable)]
            es += self.duty_entries(tax, inter, "Output", +1)
            rounding = round(r["total"] - taxable - tax, 2)
            if rounding:
                es.append(_entry(ROUND_OFF, rounding))
            self.add("Sales", r["invoice_no"], r["sale_date"], r["pname"], es, f"Bill {r['invoice_no']}",
                     pgstin=self.parties[r["pname"]]["gstin"], pos=st)
            if r["advance_amount"] > 0:
                led = self.money_ledger(r["advance_method"])
                self.add("Receipt", f"{r['invoice_no']}-R", r["sale_date"], r["pname"],
                         [_entry(led, -r["advance_amount"]), _entry(r["pname"], r["advance_amount"], True)],
                         f"Paid at billing ({r['advance_method']})")
        # credit notes (customer returns)
        for r in c.execute(
                """SELECT r.*, COALESCE(p.name,'Walk-in Customer') AS pname, p.gstin, p.state_code FROM returns r
                   LEFT JOIN parties p ON p.id = r.party_id
                   WHERE r.kind='customer' AND date(r.created_at) BETWEEN ? AND ? ORDER BY r.id""", (self.d1, self.d2)):
            reg, st, inter = self.party(r["pname"], "customer", r["gstin"], r["state_code"])
            es = [_entry(r["pname"], r["total"], True), _entry(o["sales"], -r["taxable_value"])]
            es += self.duty_entries(r["gst_amount"], inter, "Output", -1)
            rounding = round(r["total"] - r["taxable_value"] - r["gst_amount"], 2)
            if rounding:
                es.append(_entry(ROUND_OFF, -rounding))
            self.add("Credit Note", r["note_no"], r["created_at"], r["pname"], es, f"Return against {r['ref_no']}",
                     reference=r["ref_no"] or "", pgstin=self.parties[r["pname"]]["gstin"], pos=st)
            if r["refund_method"] != "adjust" and r["total"] > 0:
                self.add("Payment", f"{r['note_no']}-R", r["created_at"], r["pname"],
                         [_entry(r["pname"], -r["total"], True), _entry(self.money_ledger(r["refund_method"]), r["total"])],
                         f"Refund for {r['note_no']}")
        # purchases
        for r in c.execute(
                """SELECT pu.*, COALESCE(p.name,'Cash Purchase') AS pname, p.gstin, p.state_code FROM purchases pu
                   LEFT JOIN parties p ON p.id = pu.supplier_id
                   WHERE date(pu.purchase_date) BETWEEN ? AND ? ORDER BY pu.id""", (self.d1, self.d2)):
            reg, st, inter = self.party(r["pname"], "supplier", r["gstin"], r["state_code"])
            es = [_entry(r["pname"], r["total"], True)]
            if reg:
                es += [_entry(o["purchase"], -r["taxable_value"])] + self.duty_entries(r["gst_amount"], inter, "Input", -1)
            else:                                           # no GSTIN -> no input credit; the tax is part of the cost
                es.append(_entry(o["purchase"], -(r["taxable_value"] + r["gst_amount"])))
            rounding = round(r["total"] - r["taxable_value"] - r["gst_amount"], 2)
            if rounding:
                es.append(_entry(ROUND_OFF, -rounding))
            num = f"PUR-{r['id']:05d}"
            self.add("Purchase", num, r["purchase_date"], r["pname"], es,
                     f"Supplier invoice {r['bill_no']}" if r["bill_no"] else "", reference=r["bill_no"] or "",
                     pgstin=self.parties[r["pname"]]["gstin"], pos=st)
            if r["advance_amount"] > 0:
                self.add("Payment", f"{num}-P", r["purchase_date"], r["pname"],
                         [_entry(r["pname"], -r["advance_amount"], True),
                          _entry(self.money_ledger(r["advance_method"]), r["advance_amount"])], "Paid at purchase")
        # debit notes (returns to suppliers)
        for r in c.execute(
                """SELECT r.*, COALESCE(p.name,'Cash Purchase') AS pname, p.gstin, p.state_code FROM returns r
                   LEFT JOIN parties p ON p.id = r.party_id
                   WHERE r.kind='supplier' AND date(r.created_at) BETWEEN ? AND ? ORDER BY r.id""", (self.d1, self.d2)):
            reg, st, inter = self.party(r["pname"], "supplier", r["gstin"], r["state_code"])
            es = [_entry(r["pname"], -r["total"], True)]
            if reg:
                es += [_entry(o["purchase"], r["taxable_value"])] + self.duty_entries(r["gst_amount"], inter, "Input", +1)
            else:
                es.append(_entry(o["purchase"], r["taxable_value"] + r["gst_amount"]))
            rounding = round(r["total"] - r["taxable_value"] - r["gst_amount"], 2)
            if rounding:
                es.append(_entry(ROUND_OFF, rounding))
            self.add("Debit Note", r["note_no"], r["created_at"], r["pname"], es, f"Return against {r['ref_no']}",
                     reference=r["ref_no"] or "", pgstin=self.parties[r["pname"]]["gstin"], pos=st)
            if r["refund_method"] != "adjust" and r["total"] > 0:
                self.add("Receipt", f"{r['note_no']}-R", r["created_at"], r["pname"],
                         [_entry(self.money_ledger(r["refund_method"]), -r["total"]), _entry(r["pname"], r["total"], True)],
                         f"Refund for {r['note_no']}")
        # money received from customers / paid to suppliers later
        for r in c.execute(
                """SELECT pa.*, p.name AS pname, p.type, p.gstin, p.state_code FROM payments pa JOIN parties p ON p.id = pa.party_id
                   WHERE date(pa.paid_on) BETWEEN ? AND ? ORDER BY pa.id""", (self.d1, self.d2)):
            self.party(r["pname"], r["type"], r["gstin"], r["state_code"])
            led = self.money_ledger(r["mode"])
            if r["type"] == "customer":
                self.add("Receipt", f"RCT-{r['id']:05d}", r["paid_on"], r["pname"],
                         [_entry(led, -r["amount"]), _entry(r["pname"], r["amount"], True)], r["note"] or "Payment received")
            else:
                self.add("Payment", f"PMT-{r['id']:05d}", r["paid_on"], r["pname"],
                         [_entry(r["pname"], -r["amount"], True), _entry(led, r["amount"])], r["note"] or "Payment made")
        if not self.shop_gstin:
            self.warnings.append("Your shop GSTIN is not set in Settings, so every sale is booked as CGST + SGST.")
        return self

    # ---- XML
    def _envelope(self, report, messages):
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE>\n <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>\n'
                ' <BODY><IMPORTDATA>\n  <REQUESTDESC><REPORTNAME>' + report + '</REPORTNAME><STATICVARIABLES>'
                '<SVCURRENTCOMPANY>' + escape(self.o["company"]) + '</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>\n'
                '  <REQUESTDATA>\n' + "".join(messages) + '  </REQUESTDATA>\n </IMPORTDATA></BODY>\n</ENVELOPE>\n')

    def _ledger(self, name, parent, extra=""):
        n = escape(name)
        return (f'   <TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{escape(name, {chr(34): "&quot;"})}" ACTION="Create">'
                f'<NAME.LIST><NAME>{n}</NAME></NAME.LIST><PARENT>{escape(parent)}</PARENT>{extra}</LEDGER></TALLYMESSAGE>\n')

    def masters_xml(self):
        o, m = self.o, []
        m.append(self._ledger(o["sales"], "Sales Accounts"))
        m.append(self._ledger(o["purchase"], "Purchase Accounts"))
        m.append(self._ledger(o["cash"], "Cash-in-Hand"))
        m.append(self._ledger(o["bank"], "Bank Accounts"))
        m.append(self._ledger(ROUND_OFF, "Indirect Expenses"))
        for prefix, head in sorted(self.used_duty):
            m.append(self._ledger(f"{prefix} {DUTY[head][0]}", "Duties & Taxes",
                                  f"<TAXTYPE>GST</TAXTYPE><GSTDUTYHEAD>{DUTY[head][1]}</GSTDUTYHEAD>"))
        for name, p in sorted(self.parties.items()):
            extra = ("<ISBILLWISEON>No</ISBILLWISEON><COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>"
                     + (f"<LEDSTATENAME>{escape(STATES[p['state']])}</LEDSTATENAME>" if p["state"] in STATES else "")
                     + (f"<GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE><PARTYGSTIN>{p['gstin']}</PARTYGSTIN>"
                        if p["gstin"] else "<GSTREGISTRATIONTYPE>Unregistered</GSTREGISTRATIONTYPE>"))
            m.append(self._ledger(name, "Sundry Debtors" if p["type"] == "customer" else "Sundry Creditors", extra))
        return self._envelope("All Masters", m)

    def vouchers_xml(self):
        out = []
        for v in self.vouchers:
            lines = "".join(
                f'    <ALLLEDGERENTRIES.LIST><LEDGERNAME>{escape(e["name"])}</LEDGERNAME>'
                f'<ISDEEMEDPOSITIVE>{"Yes" if e["amount"] < 0 else "No"}</ISDEEMEDPOSITIVE>'
                f'<ISPARTYLEDGER>{"Yes" if e["party"] else "No"}</ISPARTYLEDGER><AMOUNT>{_amt(e["amount"])}</AMOUNT>'
                f'</ALLLEDGERENTRIES.LIST>\n' for e in v["entries"])
            invoice = v["type"] in ("Sales", "Purchase", "Credit Note", "Debit Note")
            head = (f'    <DATE>{_date(v["date"])}</DATE><VOUCHERTYPENAME>{v["type"]}</VOUCHERTYPENAME>'
                    f'<VOUCHERNUMBER>{escape(v["number"])}</VOUCHERNUMBER><PARTYLEDGERNAME>{escape(v["party"])}</PARTYLEDGERNAME>'
                    + (f'<REFERENCE>{escape(v["reference"])}</REFERENCE>' if v["reference"] else "")
                    + (f'<PARTYGSTIN>{v["gstin"]}</PARTYGSTIN>' if v["gstin"] else "")
                    + (f'<PLACEOFSUPPLY>{escape(STATES[v["pos"]])}</PLACEOFSUPPLY>' if invoice and v["pos"] in STATES else "")
                    + (f'<CMPGSTIN>{self.shop_gstin}</CMPGSTIN>' if invoice and self.shop_gstin else "")
                    + f'<ISINVOICE>{"Yes" if invoice else "No"}</ISINVOICE>'
                    + (f'<NARRATION>{escape(v["narration"])}</NARRATION>' if v["narration"] else "") + "\n")
            out.append(f'   <TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{v["type"]}" ACTION="Create" '
                       f'OBJVIEW="Accounting Voucher View">\n{head}{lines}   </VOUCHER></TALLYMESSAGE>\n')
        return self._envelope("Vouchers", out)


def build(con, d1, d2, opts=None):
    """-> Builder with .vouchers, .counts, .warnings and .masters_xml() / .vouchers_xml()."""
    return Builder(con, opts or load_opts(con), d1, d2).run()


def write_files(b, folder):
    """Writes the masters file and the vouchers file; returns (masters path, vouchers path)."""
    folder = Path(folder)
    tag = f"{b.d1}_to_{b.d2}"
    mp, vp = folder / f"ShopDesk_Tally_1_Ledgers_{tag}.xml", folder / f"ShopDesk_Tally_2_Vouchers_{tag}.xml"
    mp.write_text(b.masters_xml(), encoding="utf-8")
    vp.write_text(b.vouchers_xml(), encoding="utf-8")
    return mp, vp

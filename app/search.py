"""Search everywhere: one query across products, customers, suppliers, invoices and batches."""


def search_all(con, text, per_group=6):
    """Returns [(group title, [(main text, small text, kind, id)])] with empty groups left out."""
    t = (text or "").strip()
    if len(t) < 1:
        return []
    like = f"%{t}%"
    groups = []

    def add(title, rows):
        if rows:
            groups.append((title, rows))

    add("Products", [(r["name"], f"{r['category'] or 'No category'}  |  {r['stock'] or 0} in stock", "product", r["id"])
                     for r in con.execute(
        """SELECT p.id, p.name, p.category, (SELECT SUM(b.qty + COALESCE((SELECT SUM(gs.qty) FROM godown_stock gs
                  WHERE gs.batch_id = b.id), 0)) FROM batches b WHERE b.product_id = p.id) AS stock
           FROM products p WHERE p.active = 1 AND (p.name LIKE ? OR IFNULL(p.sku,'') LIKE ? OR IFNULL(p.barcode,'') LIKE ?
                  OR IFNULL(p.hsn_code,'') LIKE ?) ORDER BY p.name LIMIT ?""", (like, like, like, like, per_group))])
    for ptype, title in (("customer", "Customers"), ("supplier", "Suppliers")):
        add(title, [(r["name"], "  |  ".join(x for x in (r["phone"], r["gstin"],
                     f"owes ₹{r['balance']:,.0f}" if ptype == "customer" and r["balance"] > 0.5
                     else f"we owe ₹{r['balance']:,.0f}" if ptype == "supplier" and r["balance"] > 0.5 else "") if x),
                     ptype, r["id"])
                    for r in con.execute(
            """SELECT id, name, phone, gstin, balance FROM parties WHERE type = ? AND (name LIKE ? OR IFNULL(phone,'') LIKE ?
                      OR IFNULL(gstin,'') LIKE ?) ORDER BY name LIMIT ?""", (ptype, like, like, like, per_group))])
    add("Invoices", [(r["invoice_no"], f"{r['sale_date'][:10]}  |  {r['cust']}  |  ₹{r['total']:,.2f}", "invoice", r["id"])
                     for r in con.execute(
        """SELECT s.id, s.invoice_no, s.sale_date, s.total, COALESCE(c.name, 'Walk-in Customer') AS cust
           FROM sales s LEFT JOIN parties c ON c.id = s.customer_id
           WHERE s.invoice_no LIKE ? OR IFNULL(c.name,'') LIKE ? ORDER BY s.id DESC LIMIT ?""", (like, like, per_group))])
    add("Batches", [(f"{r['name']}  -  batch {r['batch_no']}",
                     f"expires {r['expiry_date'] or 'never'}  |  {r['qty']} in shop", "batch", r["id"])
                    for r in con.execute(
        """SELECT b.id, b.batch_no, b.expiry_date, b.qty, p.name FROM batches b JOIN products p ON p.id = b.product_id
           WHERE p.active = 1 AND b.batch_no LIKE ? ORDER BY b.expiry_date LIMIT ?""", (like, per_group))])
    return groups

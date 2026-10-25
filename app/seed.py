"""Fills the database with demo data so the dashboard has something to show.
Run:  python -m app.seed
"""
import random
from datetime import date, timedelta

from .db import connect

PRODUCTS = [
    ("Paracetamol 500mg", "Medicine", 30), ("Cough Syrup 100ml", "Medicine", 15),
    ("Vitamin C Tablets", "Supplement", 20), ("Hand Sanitizer 500ml", "Hygiene", 25),
    ("Basmati Rice 5kg", "Grocery", 20), ("Olive Oil 1L", "Grocery", 12),
    ("Green Tea Box", "Grocery", 15), ("Protein Bar", "Snacks", 40),
    ("Orange Juice 1L", "Beverage", 18), ("Multigrain Bread", "Bakery", 25),
    ("Antacid Gel", "Medicine", 15), ("Face Mask (50pc)", "Hygiene", 10),
]


def run():
    con = connect()
    if con.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
        print("Database already has data - skipping seed.")
        return
    random.seed(7)
    from . import audit
    audit.uninstall(con)          # demo data is not 'real' activity; triggers come back on the next start
    today = date.today()

    con.execute("INSERT INTO parties(type,name,phone) VALUES('supplier','Sharma Distributors','9800000000')")

    for i, (name, cat, reorder) in enumerate(PRODUCTS, 1):
        con.execute("INSERT INTO products(sku,name,category,reorder_level,gst_percent) VALUES(?,?,?,?,?)",
                    (f"SKU{i:03d}", name, cat, reorder, 12))
        pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for b in range(random.randint(1, 2)):
            cost = random.randint(20, 400)
            days = random.choice([-2, 4, 6, 12, 25, 45, 90, 200, 365])
            qty = random.choice([0, 5, 8, 14, 30, 60, 120]) if i > 2 else random.choice([6, 40])
            con.execute(
                """INSERT INTO batches(product_id,batch_no,expiry_date,qty,cost_price,mrp,sale_price,export_ready,supplier_id)
                   VALUES(?,?,?,?,?,?,?,?,2)""",
                (pid, f"B{i}{b}", (today + timedelta(days=days)).isoformat(), qty, cost,
                 round(cost * 1.5), round(cost * 1.35), 1 if random.random() < 0.2 else 0))

    batches = con.execute("SELECT id, product_id, cost_price, sale_price FROM batches").fetchall()
    n = 0
    for d in range(0, 14):
        for _ in range(random.randint(2, 7)):
            n += 1
            when = f"{(today - timedelta(days=d)).isoformat()} {random.randint(9, 19):02d}:{random.randint(0, 59):02d}:00"
            con.execute("INSERT INTO sales(invoice_no,customer_id,sale_date) VALUES(?,1,?)", (f"INV-{n:05d}", when))
            sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            total = 0
            # first 4 products sell often; the rest rarely -> gives fast + slow movers
            for b in random.sample(batches[:5] if random.random() < .8 else batches, random.randint(1, 3)):
                qty = random.randint(1, 6)
                line = qty * b["sale_price"]
                total += line
                con.execute(
                    """INSERT INTO sale_items(sale_id,product_id,batch_id,qty,unit_price,cost_price,line_total)
                       VALUES(?,?,?,?,?,?,?)""",
                    (sid, b["product_id"], b["id"], qty, b["sale_price"], b["cost_price"], line))
            con.execute("""UPDATE sales SET subtotal=?, total=?, paid=?, advance_amount=?, payment_method='cash',
                           advance_method='cash', balance_due=0 WHERE id=?""", (total, total, total, total, sid))
    con.commit()
    from . import fy
    fy.sync(con)
    print("Demo data added.")


if __name__ == "__main__":
    run()

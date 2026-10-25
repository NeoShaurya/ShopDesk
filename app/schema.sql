PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sku             TEXT UNIQUE,
    name            TEXT NOT NULL,
    category        TEXT,
    unit            TEXT DEFAULT 'pcs',
    hsn_code        TEXT,
    gst_percent     REAL DEFAULT 0,
    reorder_level   INTEGER DEFAULT 10,      -- low-stock threshold
    active          INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- One row per batch: this is where stock, expiry and prices live.
CREATE TABLE IF NOT EXISTS batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    batch_no        TEXT NOT NULL,
    expiry_date     TEXT,                    -- YYYY-MM-DD, NULL = no expiry
    qty             INTEGER NOT NULL DEFAULT 0,
    cost_price      REAL NOT NULL DEFAULT 0,
    mrp             REAL NOT NULL DEFAULT 0,
    sale_price      REAL NOT NULL DEFAULT 0,
    export_ready    INTEGER DEFAULT 0,       -- 1 = marked ready for export/sale today
    supplier_id     INTEGER REFERENCES parties(id),
    received_on     TEXT DEFAULT CURRENT_DATE,
    UNIQUE(product_id, batch_no)
);

-- Customers and suppliers share one table.
CREATE TABLE IF NOT EXISTS parties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK(type IN ('customer','supplier')),
    name        TEXT NOT NULL,
    phone       TEXT,
    email       TEXT,
    address     TEXT,
    gstin       TEXT,
    balance     REAL DEFAULT 0,              -- +ve = they owe us (customer) / we owe them (supplier)
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no    TEXT UNIQUE NOT NULL,
    customer_id   INTEGER REFERENCES parties(id),
    sale_date     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    subtotal      REAL NOT NULL DEFAULT 0,
    discount      REAL NOT NULL DEFAULT 0,
    tax           REAL NOT NULL DEFAULT 0,
    total         REAL NOT NULL DEFAULT 0,
    paid          REAL NOT NULL DEFAULT 0,
    payment_mode  TEXT DEFAULT 'cash',
    tax_inclusive INTEGER NOT NULL DEFAULT 0   -- 1 = entered prices already include GST
);

CREATE TABLE IF NOT EXISTS sale_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id     INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    qty         INTEGER NOT NULL,
    unit_price  REAL NOT NULL,
    cost_price  REAL NOT NULL,               -- snapshot, so profit reports stay correct
    discount    REAL DEFAULT 0,
    gst_percent REAL DEFAULT 0,
    line_total  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS purchases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_no       TEXT,
    supplier_id   INTEGER REFERENCES parties(id),
    purchase_date TEXT NOT NULL DEFAULT (date('now','localtime')),
    total         REAL NOT NULL DEFAULT 0,
    paid          REAL NOT NULL DEFAULT 0
);

-- Every stock change is logged (in / out / adjustment / expired write-off).
CREATE TABLE IF NOT EXISTS stock_movements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    change_qty  INTEGER NOT NULL,            -- +in / -out
    reason      TEXT NOT NULL,               -- purchase | sale | adjustment | expired | return
    ref_id      INTEGER,                     -- sale_id / purchase_id
    note        TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS payments (          -- money received from customers / paid to suppliers
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id  INTEGER NOT NULL REFERENCES parties(id),
    amount    REAL NOT NULL,
    mode      TEXT DEFAULT 'cash',
    note      TEXT,
    paid_on   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_batches_expiry  ON batches(expiry_date);
CREATE INDEX IF NOT EXISTS idx_batches_product ON batches(product_id);
CREATE INDEX IF NOT EXISTS idx_sales_date      ON sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_items_product   ON sale_items(product_id);

-- ---------- purchase lines, returns, credit / debit notes ----------
CREATE TABLE IF NOT EXISTS purchase_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id   INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products(id),
    batch_id      INTEGER NOT NULL REFERENCES batches(id),
    qty           INTEGER NOT NULL,
    cost_price    REAL NOT NULL,              -- per unit, excluding GST
    mrp           REAL DEFAULT 0,
    gst_percent   REAL DEFAULT 0,
    hsn_code      TEXT,
    expiry_date   TEXT,
    taxable_value REAL NOT NULL,
    gst_amount    REAL NOT NULL,
    line_total    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS returns (            -- one row per credit note (customer) or debit note (supplier)
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK(kind IN ('customer','supplier')),
    note_no       TEXT UNIQUE NOT NULL,         -- CN-00001 / DN-00001
    ref_id        INTEGER NOT NULL,             -- sales.id or purchases.id
    ref_no        TEXT,                         -- invoice number printed on the note
    party_id      INTEGER REFERENCES parties(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    reason        TEXT,
    restocked     INTEGER NOT NULL DEFAULT 1,
    refund_method TEXT NOT NULL DEFAULT 'adjust',   -- adjust (against balance) | cash | upi | card
    taxable_value REAL NOT NULL DEFAULT 0,
    gst_amount    REAL NOT NULL DEFAULT 0,
    total         REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS return_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id     INTEGER NOT NULL REFERENCES returns(id) ON DELETE CASCADE,
    ref_item_id   INTEGER NOT NULL,             -- sale_items.id or purchase_items.id
    product_id    INTEGER NOT NULL REFERENCES products(id),
    batch_id      INTEGER NOT NULL REFERENCES batches(id),
    qty           INTEGER NOT NULL,
    unit_price    REAL NOT NULL,                -- per unit, excluding GST
    gst_percent   REAL DEFAULT 0,
    hsn_code      TEXT,
    taxable_value REAL NOT NULL,
    gst_amount    REAL NOT NULL,
    line_total    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pitems_purchase ON purchase_items(purchase_id);
CREATE INDEX IF NOT EXISTS idx_ritems_ref      ON return_items(ref_item_id);
CREATE INDEX IF NOT EXISTS idx_returns_ref     ON returns(kind, ref_id);

-- ---------- price lists, schemes ----------
CREATE TABLE IF NOT EXISTS price_lists (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT UNIQUE NOT NULL,
    basis          TEXT NOT NULL DEFAULT 'sale_price' CHECK(basis IN ('sale_price','mrp')),
    adjust_percent REAL NOT NULL DEFAULT 0          -- e.g. -5 = 5% below the basis price
);

CREATE TABLE IF NOT EXISTS price_list_items (        -- fixed price for one product in one list
    list_id    INTEGER NOT NULL REFERENCES price_lists(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price      REAL NOT NULL,
    PRIMARY KEY (list_id, product_id)
);

CREATE TABLE IF NOT EXISTS schemes (                 -- buy X get Y free
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    buy_qty    INTEGER NOT NULL,
    free_qty   INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

-- ---------- godowns (stock locations) ----------
-- batches.qty is the stock in the MAIN location (the shop counter, where bills are made).
-- godown_stock holds the same batches stored in OTHER locations. Total stock = batches.qty + godown_stock.
CREATE TABLE IF NOT EXISTS godowns (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT UNIQUE NOT NULL,
    is_main INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS godown_stock (
    godown_id INTEGER NOT NULL REFERENCES godowns(id),
    batch_id  INTEGER NOT NULL REFERENCES batches(id),
    qty       INTEGER NOT NULL DEFAULT 0,       -- rows stay at 0 so we remember the product is kept here
    PRIMARY KEY (godown_id, batch_id)
);

CREATE TABLE IF NOT EXISTS stock_transfers (     -- every movement between locations, and receipts into a godown
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   INTEGER NOT NULL REFERENCES batches(id),
    from_id    INTEGER REFERENCES godowns(id),  -- NULL = came from a supplier
    to_id      INTEGER REFERENCES godowns(id),  -- NULL = written off
    qty        INTEGER NOT NULL,
    note       TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- ---------- unsaved work, kept automatically so a power cut or crash doesn't lose a half-made bill ----------
CREATE TABLE IF NOT EXISTS drafts (
    kind     TEXT PRIMARY KEY,                 -- 'bill' | 'purchase'
    data     TEXT NOT NULL,                    -- JSON
    saved_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- post-dated cheques ----------
CREATE TABLE IF NOT EXISTS pdcs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK(kind IN ('received','issued')),     -- received from a customer / issued to a supplier
    party_id     INTEGER NOT NULL REFERENCES parties(id),
    cheque_no    TEXT NOT NULL,
    bank         TEXT NOT NULL DEFAULT '',
    cheque_date  TEXT NOT NULL,                                          -- the date written on the cheque
    amount       REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','deposited','cleared','bounced')),
    remarks      TEXT,
    payment_id   INTEGER REFERENCES payments(id),                       -- set when it clears (the money is then recorded)
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    status_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_pdcs_date ON pdcs(cheque_date);

-- ---------- financial years (India: 1 April to 31 March) ----------
CREATE TABLE IF NOT EXISTS financial_years (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT UNIQUE NOT NULL,            -- 2026-27
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    closed_at  TEXT
);

-- ---------- cheques printed from the app (a record of what was printed) ----------
CREATE TABLE IF NOT EXISTS cheque_prints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    printed_at  TEXT DEFAULT (datetime('now','localtime')),
    payment_id  INTEGER REFERENCES payments(id),
    party_id    INTEGER REFERENCES parties(id),
    payee       TEXT NOT NULL,
    amount      REAL NOT NULL,
    cheque_no   TEXT,
    cheque_date TEXT NOT NULL,
    template    TEXT
);

-- ---------- shop expenses (rent, salary, electricity ...), for the profit & loss and the cash / bank books ----------
CREATE TABLE IF NOT EXISTS expenses (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    spent_on  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    category  TEXT NOT NULL,
    amount    REAL NOT NULL CHECK(amount > 0),
    mode      TEXT NOT NULL DEFAULT 'cash',             -- cash | upi | card | cheque | bank transfer
    note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(spent_on);

-- Dummy schema for the NL-to-SQL agentic system demo.
-- A small retail domain: customers, products, orders, order_items.

CREATE TABLE IF NOT EXISTS customer (
    customer_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    email           TEXT NOT NULL,
    phone           TEXT,
    city            TEXT,
    country         TEXT,
    signup_date     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product (
    product_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name    TEXT NOT NULL,
    category        TEXT NOT NULL,
    unit_price      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL REFERENCES customer(customer_id),
    order_date      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS order_item (
    order_item_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES product(product_id),
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL
);

-- Authentication + per-user table authorization (auth.py).
-- role: viewer (guarded SELECT-only, no SQL Editor unguarded mode, no
-- imports) | editor (today's regular user: owns tables they create) |
-- admin (today's superuser: sees/can do everything). is_superuser is kept
-- as a derived column (role = 'admin') so existing code that only knows
-- about the old boolean keeps working unchanged.
CREATE TABLE IF NOT EXISTS app_user (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    salt            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('viewer', 'editor', 'admin')),
    is_superuser    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

-- Tracks who created each non-seed table. A table with no row here is
-- "public" (visible to everyone); a table with a row here is visible only
-- to its owner and to superusers.
CREATE TABLE IF NOT EXISTS table_ownership (
    table_name      TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- Human-in-the-loop review queue, used by human_review/ module.
-- sql_attempts holds the full JSON list of {attempt, sql, valid, error}
-- dicts -- not fixed columns -- since the number of attempts before
-- escalation depends on config/guardrail_policy.yaml's
-- escalation.max_regeneration_attempts, not a hardcoded count of 2.
CREATE TABLE IF NOT EXISTS review_queue (
    review_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    user_query_masked TEXT NOT NULL,
    sql_attempts    TEXT NOT NULL,
    schema_context  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewer        TEXT,
    decision_sql    TEXT,
    decision_reason TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT
);

-- Trade History SQLite Schema
-- All monetary amounts stored in their native currency.
-- Multi-currency handled by DuckDB FX rates at query time.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Accounts ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY,
    institution      TEXT    NOT NULL,   -- 'CIBC' | 'HSBC' | 'RBC' | 'TD'
    account_id       TEXT    NOT NULL UNIQUE,
    account_type     TEXT    NOT NULL,   -- 'margin' | 'tfsa' | 'rrsp' | 'managed' | ...
    primary_currency TEXT    NOT NULL DEFAULT 'CAD',
    first_seen_date  TEXT,               -- ISO date
    last_seen_date   TEXT                -- ISO date
);

-- Computed view: group_key used for display in UI
CREATE VIEW IF NOT EXISTS accounts_with_group_key AS
SELECT
    *,
    institution || ' | ' || account_id AS group_key
FROM accounts;

-- ── Instruments ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS instruments (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT    NOT NULL,
    exchange    TEXT,                   -- 'TSX' | 'NYSE' | 'NASDAQ' | ...
    name        TEXT,
    asset_type  TEXT    NOT NULL,       -- 'equity' | 'option' | 'mutual_fund' | 'etf' | 'cash'
    -- Option fields (NULL for equities):
    option_root TEXT,
    strike      REAL,
    expiry      TEXT,                   -- ISO date
    put_call    TEXT,                   -- 'call' | 'put'
    multiplier  INTEGER DEFAULT 100,
    UNIQUE (symbol, option_root, strike, expiry, put_call)
);

-- ── Transactions ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id INTEGER REFERENCES instruments(id),  -- NULL for cash activities
    statement_id  INTEGER REFERENCES statement_registry(id),
    trade_date    TEXT    NOT NULL,     -- ISO date
    settle_date   TEXT,                 -- ISO date, nullable
    activity      TEXT    NOT NULL,
    -- activity values: bought | sold | dividend | exercise | assignment | expired |
    --   transfer_in | transfer_out | reinvestment | contribution | withdrawal |
    --   interest | fee | withholding_tax | journalled | initial_holding | other
    quantity      REAL,
    price         REAL,
    amount        REAL    NOT NULL,     -- positive=credit, negative=debit
    currency      TEXT    NOT NULL DEFAULT 'CAD',
    commission    REAL    DEFAULT 0,
    source_file   TEXT    NOT NULL,
    raw_text      TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_account   ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_instrument ON transactions(instrument_id);
CREATE INDEX IF NOT EXISTS idx_tx_date       ON transactions(trade_date);

-- ── Transfer Pairs ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS transfer_pairs (
    id                  INTEGER PRIMARY KEY,
    from_transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    to_transaction_id   INTEGER NOT NULL REFERENCES transactions(id),
    instrument_id       INTEGER NOT NULL REFERENCES instruments(id),
    quantity            REAL    NOT NULL,
    transfer_date       TEXT    NOT NULL
);

-- ── Position State ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS position_state (
    id                 INTEGER PRIMARY KEY,
    account_id         INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id      INTEGER NOT NULL REFERENCES instruments(id),
    as_of_date         TEXT    NOT NULL,
    quantity           REAL    NOT NULL,
    book_cost          REAL,
    book_cost_currency TEXT    DEFAULT 'CAD',
    market_price       REAL,
    market_value       REAL,
    market_currency    TEXT    DEFAULT 'CAD',
    UNIQUE (account_id, instrument_id, as_of_date)
);

-- ── Quarantine ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS quarantine_transactions (
    id          INTEGER PRIMARY KEY,
    source_file TEXT    NOT NULL,
    raw_text    TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Description → Symbol Mapping ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS description_symbol_map (
    id          INTEGER PRIMARY KEY,
    institution TEXT    NOT NULL,
    description TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'holdings',  -- 'holdings' | 'manual'
    UNIQUE(institution, description)
);

-- ── Monthly Balances ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS monthly_balances (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id       INTEGER NOT NULL REFERENCES accounts(id),
    instrument_id    INTEGER NOT NULL REFERENCES instruments(id),
    year_month       TEXT    NOT NULL,          -- 'YYYY-MM'
    quantity         REAL    NOT NULL,
    avg_cost         REAL,
    market_price     REAL,                      -- per-unit price
    market_value     REAL,                      -- total value
    currency         TEXT    NOT NULL DEFAULT 'CAD',
    as_of_date       TEXT    NOT NULL,          -- actual statement date used
    statement_id     INTEGER REFERENCES statement_registry(id),
    UNIQUE(account_id, instrument_id, year_month)
);

-- ── Statement Registry ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS statement_registry (
    id                   INTEGER PRIMARY KEY,
    source_file          TEXT    NOT NULL UNIQUE,
    institution          TEXT    NOT NULL,
    account_id           TEXT,
    period_start         TEXT,
    period_end           TEXT,
    processed_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    transaction_count    INTEGER DEFAULT 0,
    status               TEXT    NOT NULL DEFAULT 'ok',
    -- status: 'ok' | 'partial' | 'error'
    opening_balance_cad  REAL,
    opening_balance_usd  REAL,
    closing_balance_cad  REAL,
    closing_balance_usd  REAL,
    balance_validated    TEXT    DEFAULT 'missing',
    -- balance_validated: 'ok' | 'mismatch' | 'missing'
    docling_json         TEXT    -- docling export_to_dict() JSON output
);

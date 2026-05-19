-- Ledger SQLite schema. Multi-currency, multi-account, stocks + options + cash.
-- All money values are stored in their native currency; the `currency` column
-- in each row records which currency that amount is in. FX conversion is a
-- presentation-layer concern only.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------------
-- INSTITUTIONS / ACCOUNTS
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS institutions (
    institution_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT NOT NULL UNIQUE,        -- e.g. RBC_DI, CIBC_IS
    display_name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    institution_id   INTEGER NOT NULL REFERENCES institutions(institution_id),
    account_number   TEXT NOT NULL,               -- as printed on statement (may be masked)
    account_type     TEXT,                        -- Cash, Margin, RRSP, TFSA, RESP, etc.
    nickname         TEXT,                        -- optional friendly label
    base_currency    TEXT NOT NULL DEFAULT 'CAD', -- account's reporting currency
    opened_on        TEXT,
    closed_on        TEXT,
    notes            TEXT,
    UNIQUE(institution_id, account_number)
);

-- Many real-world transfers are "in-kind" between two of MY accounts. This
-- table groups two transactions (out + in) into a single logical event so
-- positions can be chained across accounts (e.g. CIBC ID -> CIBC TFSA).
CREATE TABLE IF NOT EXISTS account_links (
    link_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account_id  INTEGER NOT NULL REFERENCES accounts(account_id),
    to_account_id    INTEGER NOT NULL REFERENCES accounts(account_id),
    transfer_date    TEXT NOT NULL,
    notes            TEXT
);

-- ---------------------------------------------------------------------------
-- INSTRUMENTS (equity + options + cash + others)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_type       TEXT NOT NULL CHECK (asset_type IN
                       ('equity','etf','option','bond','mutual_fund','cash','other')),
    symbol           TEXT NOT NULL,               -- e.g. AAPL, BNS.TO, SPY
    exchange         TEXT,                        -- NYSE, NASDAQ, TSX, TSXV...
    currency         TEXT NOT NULL,               -- USD, CAD
    name             TEXT,                        -- "Apple Inc."
    cusip            TEXT,
    isin             TEXT,
    -- Option contract fields (NULL for non-options)
    option_root      TEXT,
    option_expiry    TEXT,                        -- YYYY-MM-DD
    option_strike    REAL,
    option_type      TEXT CHECK (option_type IN ('CALL','PUT') OR option_type IS NULL),
    option_multiplier INTEGER DEFAULT 100,
    UNIQUE(asset_type, symbol, currency, option_expiry, option_strike, option_type)
);

CREATE INDEX IF NOT EXISTS idx_instruments_symbol ON instruments(symbol);

-- Map alternative names/symbols a statement may use to a canonical instrument.
CREATE TABLE IF NOT EXISTS instrument_aliases (
    alias_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id    INTEGER NOT NULL REFERENCES instruments(instrument_id) ON DELETE CASCADE,
    alias            TEXT NOT NULL,
    institution_id   INTEGER REFERENCES institutions(institution_id),
    UNIQUE(alias, institution_id)
);

-- Statement names that need a first-time external identifier lookup.
-- Example: CIBC mutual fund rows often print a fund name/class but no fund
-- code. Parsers store the printed name; repair can resolve it only after a
-- reviewed lookup row is marked resolved here.
CREATE TABLE IF NOT EXISTS instrument_identifier_lookups (
    lookup_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier_type    TEXT NOT NULL DEFAULT 'fund_code',
    asset_type         TEXT NOT NULL DEFAULT 'mutual_fund',
    institution_code   TEXT NOT NULL DEFAULT '',
    normalized_name    TEXT NOT NULL,
    display_name       TEXT NOT NULL,
    currency           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','resolved','not_found','ambiguous','ignored')),
    resolved_symbol    TEXT,
    resolved_exchange  TEXT,
    resolved_name      TEXT,
    evidence_url       TEXT,
    sample_description TEXT,
    first_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at       TEXT NOT NULL DEFAULT (datetime('now')),
    notes              TEXT,
    UNIQUE(identifier_type, asset_type, institution_code, normalized_name, currency)
);

CREATE INDEX IF NOT EXISTS idx_identifier_lookups_status
    ON instrument_identifier_lookups(status, identifier_type, asset_type);

-- ---------------------------------------------------------------------------
-- STATEMENTS: one row per ingested PDF (or per account inside a multi-account PDF)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_files (
    source_file_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    relpath          TEXT NOT NULL UNIQUE,        -- relative to repo root
    sha256           TEXT,
    size_bytes       INTEGER,
    page_count       INTEGER,
    is_image_only    INTEGER NOT NULL DEFAULT 0,
    parser_name      TEXT,
    parser_version   TEXT,
    parsed_at        TEXT,
    parse_status     TEXT NOT NULL DEFAULT 'pending'  -- pending|ok|partial|failed|skipped
);

CREATE TABLE IF NOT EXISTS statements (
    statement_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id   INTEGER NOT NULL REFERENCES source_files(source_file_id) ON DELETE CASCADE,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    period_start     TEXT NOT NULL,               -- YYYY-MM-DD
    period_end       TEXT NOT NULL,               -- YYYY-MM-DD
    statement_type   TEXT NOT NULL DEFAULT 'monthly', -- monthly|quarterly|annual|interim
    UNIQUE(source_file_id, account_id, period_end)
);

CREATE INDEX IF NOT EXISTS idx_statements_account_period ON statements(account_id, period_end);

-- ---------------------------------------------------------------------------
-- TRANSACTIONS: every event that affects positions or cash
-- ---------------------------------------------------------------------------
-- Transaction types we recognize:
--   buy, sell, short_sell, buy_to_cover,
--   option_buy_to_open, option_sell_to_open,
--   option_buy_to_close, option_sell_to_close,
--   option_assignment, option_exercise, option_expiration,
--   dividend, distribution, interest_income,
--   interest_expense, margin_interest,
--   transfer_in, transfer_out, journal,        -- cash or in-kind movements
--   deposit, withdrawal,                       -- external cash
--   tax_withholding,
--   fee, commission, adjustment, fx_conversion,
--   stock_split, name_change, spinoff, merger, return_of_capital
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id         INTEGER NOT NULL REFERENCES accounts(account_id),
    statement_id       INTEGER REFERENCES statements(statement_id) ON DELETE SET NULL,
    source_file_id     INTEGER REFERENCES source_files(source_file_id) ON DELETE SET NULL,

    trade_date         TEXT NOT NULL,             -- YYYY-MM-DD
    settle_date        TEXT,
    txn_type           TEXT NOT NULL,
    instrument_id      INTEGER REFERENCES instruments(instrument_id),

    quantity           REAL,                      -- signed: + buy/long, - sell/short
    price              REAL,                      -- per share / contract premium per share
    gross_amount       REAL,                      -- price * quantity * multiplier (sign per direction)
    commission         REAL DEFAULT 0,
    other_fees         REAL DEFAULT 0,
    net_amount         REAL,                      -- cash impact in `currency` (signed)
    currency           TEXT NOT NULL,             -- the currency of price/amount

    -- For transfer_in / transfer_out / journal: link to the matching event
    counterpart_account_id INTEGER REFERENCES accounts(account_id),
    counterpart_txn_id     INTEGER REFERENCES transactions(transaction_id),

    -- For tax_withholding: foreign tax % or country code
    tax_country        TEXT,
    tax_rate           REAL,

    description        TEXT,                      -- raw description from statement
    raw_line           TEXT,                      -- original line for audit
    parser_confidence  REAL DEFAULT 1.0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_txn_account_date  ON transactions(account_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_txn_instrument    ON transactions(instrument_id);
CREATE INDEX IF NOT EXISTS idx_txn_type          ON transactions(txn_type);
CREATE INDEX IF NOT EXISTS idx_txn_statement     ON transactions(statement_id);

-- Quarantine: rows we couldn't confidently parse.
CREATE TABLE IF NOT EXISTS quarantine_transactions (
    quarantine_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id   INTEGER REFERENCES source_files(source_file_id) ON DELETE CASCADE,
    account_id       INTEGER REFERENCES accounts(account_id),
    raw_line         TEXT NOT NULL,
    reason           TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- POSITION SNAPSHOTS: monthly statement holdings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id      INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL,
    instrument_id     INTEGER NOT NULL REFERENCES instruments(instrument_id),
    quantity          REAL NOT NULL,
    avg_cost          REAL,                       -- per share, native currency
    book_value        REAL,                       -- total cost in `currency`
    market_price      REAL,
    market_value      REAL,
    unrealized_pnl    REAL,
    currency          TEXT NOT NULL,
    raw_line          TEXT,
    UNIQUE(statement_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_pos_account_date ON position_snapshots(account_id, as_of_date);

-- Cash balance per (account, currency) on a statement.
CREATE TABLE IF NOT EXISTS cash_balances (
    cash_balance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id      INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL,
    currency          TEXT NOT NULL,
    opening_balance   REAL,
    closing_balance   REAL NOT NULL,
    UNIQUE(statement_id, currency)
);

CREATE INDEX IF NOT EXISTS idx_cash_account_date ON cash_balances(account_id, as_of_date);

-- Initial positions: positions held BEFORE the first ingested statement
-- (because some transactions predate the earliest statement). User-curated.
CREATE TABLE IF NOT EXISTS initial_positions (
    initial_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL,              -- date BEFORE first statement
    instrument_id     INTEGER NOT NULL REFERENCES instruments(instrument_id),
    quantity          REAL NOT NULL,
    avg_cost          REAL,
    currency          TEXT NOT NULL,
    notes             TEXT,
    UNIQUE(account_id, as_of_date, instrument_id)
);

CREATE TABLE IF NOT EXISTS initial_cash (
    initial_cash_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL,
    currency          TEXT NOT NULL,
    balance           REAL NOT NULL,
    UNIQUE(account_id, as_of_date, currency)
);

-- ---------------------------------------------------------------------------
-- POSITION-TO-TRANSACTION RECONCILIATION
-- For each (account, instrument) movement, attribute closing-month positions
-- to the underlying transactions that produced them. Built incrementally as
-- positions are reconciled.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS position_transaction_links (
    link_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id       INTEGER NOT NULL REFERENCES position_snapshots(snapshot_id) ON DELETE CASCADE,
    transaction_id    INTEGER NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    quantity_attributed REAL NOT NULL,
    UNIQUE(snapshot_id, transaction_id)
);

-- ---------------------------------------------------------------------------
-- META
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_meta (
    key              TEXT PRIMARY KEY,
    value            TEXT NOT NULL
);

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '2');

-- Ledger SQLite schema. Multi-currency, multi-account, stocks + options + cash.
-- All money values are stored in their native currency; the `currency` column
-- in each row records which currency that amount is in. FX conversion is a
-- presentation-layer concern only.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- SQLite has no enum/domain type. This reference table is the authoritative
-- private-ledger currency domain and can be extended deliberately later.
CREATE TABLE IF NOT EXISTS currencies (
    code TEXT PRIMARY KEY CHECK (code IN ('CAD','USD'))
) WITHOUT ROWID;
INSERT OR IGNORE INTO currencies(code) VALUES ('CAD'), ('USD');

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
    base_currency    TEXT NOT NULL DEFAULT 'CAD' REFERENCES currencies(code),
    opened_on        TEXT CHECK (opened_on IS NULL OR
                       (length(opened_on) = 10 AND opened_on GLOB '????-??-??')),
    closed_on        TEXT CHECK (closed_on IS NULL OR
                       (length(closed_on) = 10 AND closed_on GLOB '????-??-??')),
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
    transfer_date    TEXT NOT NULL CHECK
                       (length(transfer_date) = 10 AND transfer_date GLOB '????-??-??'),
    notes            TEXT
);

-- ---------------------------------------------------------------------------
-- INSTRUMENTS (equity + options + cash + others)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_key   TEXT NOT NULL UNIQUE,         -- canonical logical identity (ik1)
    asset_type       TEXT NOT NULL CHECK (asset_type IN
                       ('equity','etf','option','bond','mutual_fund','cash','other')),
    symbol           TEXT NOT NULL,               -- e.g. AAPL, BNS.TO, SPY
    exchange         TEXT,                        -- NYSE, NASDAQ, TSX, TSXV...
    currency         TEXT NOT NULL REFERENCES currencies(code),
    name             TEXT,                        -- "Apple Inc."
    cusip            TEXT,
    isin             TEXT,
    -- Option contract fields (NULL for non-options)
    option_root      TEXT,
    option_expiry    TEXT CHECK (option_expiry IS NULL OR
                       (length(option_expiry) = 10 AND option_expiry GLOB '????-??-??')),
    option_strike    REAL,
    option_type      TEXT CHECK (option_type IN ('CALL','PUT') OR option_type IS NULL),
    option_multiplier INTEGER DEFAULT 100,
    resolution_method TEXT,
    resolution_confidence REAL,
    CHECK (resolution_confidence IS NULL OR
           (resolution_confidence >= 0 AND resolution_confidence <= 1))
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
    currency           TEXT NOT NULL REFERENCES currencies(code),
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','resolved','not_found','ambiguous','ignored')),
    resolved_symbol    TEXT,
    resolved_exchange  TEXT,
    resolved_name      TEXT,
    evidence_url       TEXT,
    sample_description TEXT,
    first_seen_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                       CHECK (length(first_seen_at) = 20
                              AND first_seen_at GLOB '????-??-??T??:??:??Z'),
    last_seen_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                       CHECK (length(last_seen_at) = 20
                              AND last_seen_at GLOB '????-??-??T??:??:??Z'),
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
    sha256           TEXT CHECK (sha256 IS NULL OR
                       (length(sha256) = 64 AND sha256 = lower(sha256)
                        AND sha256 NOT GLOB '*[^0-9a-f]*')),
    size_bytes       INTEGER,
    page_count       INTEGER,
    is_image_only    INTEGER NOT NULL DEFAULT 0,
    parser_name      TEXT,
    parser_version   TEXT,
    parsed_at        TEXT CHECK (parsed_at IS NULL OR
                       (length(parsed_at) = 20 AND parsed_at GLOB '????-??-??T??:??:??Z')),
    parse_status     TEXT NOT NULL DEFAULT 'pending', -- compatibility summary of active/latest run
    active_ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id)
                                      ON DELETE SET NULL
);

-- Every extraction attempt is retained independently. Only a validated run
-- may be selected by source_files.active_ingestion_run_id.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id   INTEGER NOT NULL REFERENCES source_files(source_file_id)
                                 ON DELETE CASCADE,
    source_sha256    TEXT CHECK (source_sha256 IS NULL OR
                       (length(source_sha256) = 64 AND source_sha256 = lower(source_sha256)
                        AND source_sha256 NOT GLOB '*[^0-9a-f]*')),
    parser_name      TEXT,
    parser_version   TEXT,
    contract_version TEXT NOT NULL,
    schema_version   INTEGER NOT NULL,
    resolver_version TEXT,
    status           TEXT NOT NULL CHECK (status IN
                       ('pending','parsing','validated','active','failed','skipped','superseded')),
    error_summary    TEXT,
    started_at       TEXT NOT NULL CHECK
                       (length(started_at) = 20 AND started_at GLOB '????-??-??T??:??:??Z'),
    finished_at      TEXT CHECK (finished_at IS NULL OR
                       (length(finished_at) = 20 AND finished_at GLOB '????-??-??T??:??:??Z')),
    content_counts_json TEXT CHECK
                       (content_counts_json IS NULL OR json_valid(content_counts_json)),
    content_hash     TEXT CHECK (content_hash IS NULL OR
                       (length(content_hash) = 64 AND content_hash = lower(content_hash)
                        AND content_hash NOT GLOB '*[^0-9a-f]*')),
    activated_at     TEXT CHECK (activated_at IS NULL OR
                       (length(activated_at) = 20 AND activated_at GLOB '????-??-??T??:??:??Z'))
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source
    ON ingestion_runs(source_file_id, ingestion_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_runs_one_active
    ON ingestion_runs(source_file_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS statements (
    statement_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id   INTEGER NOT NULL REFERENCES source_files(source_file_id) ON DELETE CASCADE,
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(ingestion_run_id) ON DELETE CASCADE,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    statement_key    TEXT NOT NULL UNIQUE,
    period_start     TEXT NOT NULL CHECK
                       (length(period_start) = 10 AND period_start GLOB '????-??-??'),
    period_end       TEXT NOT NULL CHECK
                       (length(period_end) = 10 AND period_end GLOB '????-??-??'),
    statement_type   TEXT NOT NULL DEFAULT 'monthly', -- monthly|quarterly|annual|interim
    UNIQUE(source_file_id, account_id, period_start, period_end, statement_type)
);

CREATE INDEX IF NOT EXISTS idx_statements_account_period ON statements(account_id, period_end);
CREATE INDEX IF NOT EXISTS idx_statements_run ON statements(ingestion_run_id);

-- Source evidence is semantic provenance. Its identity is independent of
-- replaceable layout geometry; legacy single-box columns remain readable.
CREATE TABLE IF NOT EXISTS source_evidence (
    evidence_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_key     TEXT NOT NULL UNIQUE,
    source_file_id   INTEGER NOT NULL REFERENCES source_files(source_file_id) ON DELETE CASCADE,
    ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id) ON DELETE CASCADE,
    row_kind         TEXT NOT NULL,
    occurrence       INTEGER NOT NULL,
    page_number      INTEGER,
    line_number      INTEGER,
    raw_text         TEXT,
    bbox_json        TEXT CHECK (bbox_json IS NULL OR json_valid(bbox_json)),
    words_json       TEXT CHECK (words_json IS NULL OR json_valid(words_json)),
    parser_rule      TEXT,
    parser_version   TEXT,
    CHECK (page_number IS NULL OR page_number >= 1),
    CHECK (line_number IS NULL OR line_number >= 1),
    UNIQUE(source_file_id, row_kind, occurrence, ingestion_run_id)
);

CREATE INDEX IF NOT EXISTS idx_source_evidence_run ON source_evidence(ingestion_run_id);

-- Geometry is derived after semantic parsing and can be rebuilt without
-- changing transactions or evidence identity.
CREATE TABLE IF NOT EXISTS source_pages (
    source_page_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id   INTEGER NOT NULL REFERENCES source_files(source_file_id) ON DELETE CASCADE,
    ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(ingestion_run_id) ON DELETE CASCADE,
    extractor_version TEXT NOT NULL,
    page_number      INTEGER NOT NULL CHECK (page_number >= 1),
    width            REAL NOT NULL CHECK (width > 0),
    height           REAL NOT NULL CHECK (height > 0),
    UNIQUE(ingestion_run_id, extractor_version, page_number)
);

CREATE TABLE IF NOT EXISTS source_lines (
    source_line_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_page_id   INTEGER NOT NULL REFERENCES source_pages(source_page_id) ON DELETE CASCADE,
    line_number      INTEGER NOT NULL CHECK (line_number >= 1),
    raw_text         TEXT NOT NULL,
    normalized_text_hash TEXT NOT NULL CHECK
                       (length(normalized_text_hash) = 64
                        AND normalized_text_hash NOT GLOB '*[^0-9a-f]*'),
    x0               REAL NOT NULL,
    top              REAL NOT NULL,
    x1               REAL NOT NULL,
    bottom           REAL NOT NULL,
    words_json       TEXT CHECK (words_json IS NULL OR json_valid(words_json)),
    CHECK (x1 >= x0 AND bottom >= top),
    UNIQUE(source_page_id, line_number)
);

CREATE TABLE IF NOT EXISTS source_evidence_geometry (
    evidence_id      INTEGER PRIMARY KEY REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
    extractor_version TEXT NOT NULL,
    source_sha256    TEXT NOT NULL CHECK
                       (length(source_sha256) = 64
                        AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
    status           TEXT NOT NULL CHECK
                       (status IN ('exact','unique_tokens','ambiguous','unmatched','no_coordinates')),
    match_method     TEXT,
    confidence       REAL CHECK (confidence IS NULL OR
                       (confidence >= 0 AND confidence <= 1)),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                       CHECK (length(updated_at) = 20
                              AND updated_at GLOB '????-??-??T??:??:??Z')
);

CREATE TABLE IF NOT EXISTS source_evidence_lines (
    evidence_id      INTEGER NOT NULL REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
    source_line_id   INTEGER NOT NULL REFERENCES source_lines(source_line_id) ON DELETE CASCADE,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    token_start      INTEGER,
    token_end        INTEGER,
    CHECK (token_start IS NULL OR token_start >= 0),
    CHECK (token_end IS NULL OR token_end >= token_start),
    PRIMARY KEY(evidence_id, source_line_id),
    UNIQUE(evidence_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_source_lines_page ON source_lines(source_page_id, line_number);
CREATE INDEX IF NOT EXISTS idx_evidence_lines_line ON source_evidence_lines(source_line_id);

-- A statement may contain multiple independently complete currency/section
-- scopes. Consumers may clear an omitted holding only when can_clear_omitted=1.
CREATE TABLE IF NOT EXISTS snapshot_sets (
    snapshot_set_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id     INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date       TEXT NOT NULL CHECK
                       (length(as_of_date) = 10 AND as_of_date GLOB '????-??-??'),
    currency         TEXT NOT NULL REFERENCES currencies(code),
    section_type     TEXT NOT NULL CHECK (section_type IN ('positions','cash','summary')),
    scope_key        TEXT NOT NULL DEFAULT 'default',
    completeness     TEXT NOT NULL CHECK (completeness IN
                       ('complete','partial','absent','unknown')),
    can_clear_omitted INTEGER GENERATED ALWAYS AS
                       (CASE WHEN completeness = 'complete' THEN 1 ELSE 0 END) STORED,
    evidence_id      INTEGER REFERENCES source_evidence(evidence_id),
    reported_total   REAL,
    validation_status TEXT NOT NULL DEFAULT 'unvalidated' CHECK (validation_status IN
                       ('unvalidated','valid','warning','invalid')),
    UNIQUE(statement_id, currency, section_type, scope_key)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_sets_scope
    ON snapshot_sets(account_id, as_of_date, currency, section_type, completeness);

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
--   reinvest_dividend,
--   stock_split, stock_split_credit, stock_split_debit,
--   name_change, spinoff, merger, return_of_capital
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id         INTEGER NOT NULL REFERENCES accounts(account_id),
    statement_id       INTEGER REFERENCES statements(statement_id) ON DELETE SET NULL,
    source_file_id     INTEGER REFERENCES source_files(source_file_id) ON DELETE SET NULL,
    ingestion_run_id   INTEGER REFERENCES ingestion_runs(ingestion_run_id) ON DELETE SET NULL,
    evidence_id        INTEGER REFERENCES source_evidence(evidence_id) ON DELETE SET NULL,

    trade_date         TEXT NOT NULL CHECK
                         (length(trade_date) = 10 AND trade_date GLOB '????-??-??'),
    settle_date        TEXT CHECK (settle_date IS NULL OR
                         (length(settle_date) = 10 AND settle_date GLOB '????-??-??')),
    txn_type           TEXT NOT NULL,
    instrument_id      INTEGER REFERENCES instruments(instrument_id),

    quantity           REAL,                      -- reported quantity (compatibility name)
    position_delta     REAL,                      -- normalized signed position effect
    price              REAL,                      -- per share / contract premium per share
    gross_amount       REAL,                      -- price * quantity * multiplier (sign per direction)
    commission         REAL DEFAULT 0,
    other_fees         REAL DEFAULT 0,
    net_amount         REAL,                      -- reported/legacy signed amount
    cash_delta         REAL,                      -- normalized signed cash effect
    cash_effective_date TEXT CHECK (cash_effective_date IS NULL OR
                         (length(cash_effective_date) = 10
                          AND cash_effective_date GLOB '????-??-??')),
    currency           TEXT NOT NULL REFERENCES currencies(code),

    -- For transfer_in / transfer_out / journal: link to the matching event
    counterpart_account_id INTEGER REFERENCES accounts(account_id),
    counterpart_txn_id     INTEGER REFERENCES transactions(transaction_id),

    -- For tax_withholding: foreign tax % or country code
    tax_country        TEXT,
    tax_rate           REAL,

    description        TEXT,                      -- raw description from statement
    raw_line           TEXT,                      -- original line for audit
    parser_confidence  REAL DEFAULT 1.0,
    resolution_method  TEXT,
    resolution_confidence REAL,
    resolution_evidence_id INTEGER REFERENCES source_evidence(evidence_id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                         CHECK (length(created_at) = 20
                                AND created_at GLOB '????-??-??T??:??:??Z')
);

CREATE INDEX IF NOT EXISTS idx_txn_account_date  ON transactions(account_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_txn_instrument    ON transactions(instrument_id);
CREATE INDEX IF NOT EXISTS idx_txn_type          ON transactions(txn_type);
CREATE INDEX IF NOT EXISTS idx_txn_statement     ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_txn_run           ON transactions(ingestion_run_id);
CREATE INDEX IF NOT EXISTS idx_txn_counterpart   ON transactions(counterpart_txn_id);

-- A ticker change is a dated relationship, not an alias. The two instrument
-- rows preserve the symbols printed before and after the effective date.
-- Multiple statements/accounts may provide evidence for the same event.
CREATE TABLE IF NOT EXISTS instrument_ticker_changes (
    ticker_change_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    from_instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
    to_instrument_id   INTEGER NOT NULL REFERENCES instruments(instrument_id),
    effective_date     TEXT NOT NULL CHECK
                         (length(effective_date) = 10 AND effective_date GLOB '????-??-??'),
    conversion_ratio   REAL NOT NULL DEFAULT 1.0 CHECK (conversion_ratio > 0),
    status             TEXT NOT NULL DEFAULT 'extracted'
                       CHECK (status IN ('extracted','reviewed')),
    resolution_method  TEXT NOT NULL,
    resolution_confidence REAL NOT NULL CHECK
                          (resolution_confidence >= 0 AND resolution_confidence <= 1),
    notes              TEXT,
    CHECK (from_instrument_id <> to_instrument_id),
    UNIQUE(from_instrument_id, to_instrument_id, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_ticker_changes_from
    ON instrument_ticker_changes(from_instrument_id, effective_date);
CREATE INDEX IF NOT EXISTS idx_ticker_changes_to
    ON instrument_ticker_changes(to_instrument_id, effective_date);

CREATE TABLE IF NOT EXISTS instrument_ticker_change_sources (
    ticker_change_source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker_change_id INTEGER NOT NULL REFERENCES instrument_ticker_changes(ticker_change_id)
                                      ON DELETE CASCADE,
    transaction_id INTEGER NOT NULL UNIQUE REFERENCES transactions(transaction_id)
                                      ON DELETE CASCADE,
    evidence_id INTEGER REFERENCES source_evidence(evidence_id) ON DELETE SET NULL
);

-- Extracted relationships disappear only when their final source row does.
-- Reviewed relationships are curated state and are never removed by ingest.
CREATE TRIGGER IF NOT EXISTS cleanup_orphan_extracted_ticker_change
AFTER DELETE ON instrument_ticker_change_sources
BEGIN
    DELETE FROM instrument_ticker_changes
     WHERE ticker_change_id = OLD.ticker_change_id
       AND status = 'extracted'
       AND NOT EXISTS (
           SELECT 1 FROM instrument_ticker_change_sources s
            WHERE s.ticker_change_id = OLD.ticker_change_id
       );
END;

-- Quarantine: rows we couldn't confidently parse.
CREATE TABLE IF NOT EXISTS quarantine_transactions (
    quarantine_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id   INTEGER REFERENCES source_files(source_file_id) ON DELETE CASCADE,
    ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id) ON DELETE CASCADE,
    statement_id     INTEGER REFERENCES statements(statement_id) ON DELETE CASCADE,
    account_id       INTEGER REFERENCES accounts(account_id),
    evidence_id      INTEGER REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
    occurrence       INTEGER NOT NULL,
    raw_line         TEXT NOT NULL,
    reason           TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                       CHECK (length(created_at) = 20
                              AND created_at GLOB '????-??-??T??:??:??Z'),
    UNIQUE(ingestion_run_id, evidence_id)
);

-- ---------------------------------------------------------------------------
-- POSITION SNAPSHOTS: monthly statement holdings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id      INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    snapshot_set_id   INTEGER NOT NULL REFERENCES snapshot_sets(snapshot_set_id) ON DELETE CASCADE,
    evidence_id       INTEGER NOT NULL REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL CHECK
                        (length(as_of_date) = 10 AND as_of_date GLOB '????-??-??'),
    instrument_id     INTEGER NOT NULL REFERENCES instruments(instrument_id),
    quantity          REAL NOT NULL,
    avg_cost          REAL,                       -- per share, native currency
    book_value        REAL,                       -- total cost in `currency`
    market_price      REAL,
    market_value      REAL,
    unrealized_pnl    REAL,
    currency          TEXT NOT NULL REFERENCES currencies(code),
    raw_line          TEXT,
    UNIQUE(snapshot_set_id, instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_pos_account_date ON position_snapshots(account_id, as_of_date);

-- Cash balance per (account, currency) on a statement.
CREATE TABLE IF NOT EXISTS cash_balances (
    cash_balance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id      INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    snapshot_set_id   INTEGER NOT NULL REFERENCES snapshot_sets(snapshot_set_id) ON DELETE CASCADE,
    evidence_id       INTEGER NOT NULL REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL CHECK
                        (length(as_of_date) = 10 AND as_of_date GLOB '????-??-??'),
    currency          TEXT NOT NULL REFERENCES currencies(code),
    opening_balance   REAL,
    closing_balance   REAL NOT NULL,
    raw_line          TEXT,
    UNIQUE(snapshot_set_id)
);

CREATE INDEX IF NOT EXISTS idx_cash_account_date ON cash_balances(account_id, as_of_date);

-- Initial positions: positions held BEFORE the first ingested statement
-- (because some transactions predate the earliest statement). User-curated.
CREATE TABLE IF NOT EXISTS initial_positions (
    initial_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL CHECK
                        (length(as_of_date) = 10 AND as_of_date GLOB '????-??-??'),
    instrument_id     INTEGER NOT NULL REFERENCES instruments(instrument_id),
    quantity          REAL NOT NULL,
    avg_cost          REAL,
    currency          TEXT NOT NULL REFERENCES currencies(code),
    notes             TEXT,
    UNIQUE(account_id, as_of_date, instrument_id)
);

CREATE TABLE IF NOT EXISTS initial_cash (
    initial_cash_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    as_of_date        TEXT NOT NULL CHECK
                        (length(as_of_date) = 10 AND as_of_date GLOB '????-??-??'),
    currency          TEXT NOT NULL REFERENCES currencies(code),
    balance           REAL NOT NULL,
    notes             TEXT,
    UNIQUE(account_id, as_of_date, currency)
);

-- Annual performance-report totals such as RBC money-weighted returns.
-- These are statement-level summaries, not transaction events.
CREATE TABLE IF NOT EXISTS annual_performance_reports (
    performance_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id              INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    account_id                INTEGER NOT NULL REFERENCES accounts(account_id),
    currency                  TEXT NOT NULL REFERENCES currencies(code),
    period_start              TEXT NOT NULL CHECK
                                (length(period_start) = 10 AND period_start GLOB '????-??-??'),
    period_end                TEXT NOT NULL CHECK
                                (length(period_end) = 10 AND period_end GLOB '????-??-??'),
    since_date                TEXT CHECK (since_date IS NULL OR
                                (length(since_date) = 10 AND since_date GLOB '????-??-??')),
    beginning_market_value    REAL,
    deposits_transfers_in     REAL,
    withdrawals_transfers_out REAL,
    net_investment_return     REAL,
    ending_market_value       REAL,
    money_weighted_1y         REAL,
    money_weighted_3y         REAL,
    money_weighted_5y         REAL,
    money_weighted_10y        REAL,
    money_weighted_since      REAL,
    UNIQUE(statement_id, currency)
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

CREATE INDEX IF NOT EXISTS idx_pos_txn_links_txn ON position_transaction_links(transaction_id);

-- Explicit results store the equation and residual; they never create an
-- adjustment transaction. A component table preserves the audit trail.
CREATE TABLE IF NOT EXISTS reconciliation_results (
    reconciliation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reconciliation_key TEXT NOT NULL UNIQUE,
    ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN
                       ('position','cash','statement_total','transfer')),
    account_id       INTEGER NOT NULL REFERENCES accounts(account_id),
    statement_id     INTEGER REFERENCES statements(statement_id) ON DELETE CASCADE,
    snapshot_set_id  INTEGER REFERENCES snapshot_sets(snapshot_set_id) ON DELETE CASCADE,
    prior_snapshot_set_id INTEGER REFERENCES snapshot_sets(snapshot_set_id) ON DELETE SET NULL,
    instrument_id    INTEGER REFERENCES instruments(instrument_id),
    currency         TEXT NOT NULL REFERENCES currencies(code),
    prior_checkpoint TEXT CHECK (prior_checkpoint IS NULL OR
                       (length(prior_checkpoint) = 10 AND prior_checkpoint GLOB '????-??-??')),
    current_checkpoint TEXT CHECK (current_checkpoint IS NULL OR
                       (length(current_checkpoint) = 10 AND current_checkpoint GLOB '????-??-??')),
    opening_value    REAL,
    summed_deltas    REAL,
    expected_close   REAL,
    reported_close   REAL,
    residual         REAL,
    tolerance        REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL CHECK (status IN
                       ('reconciled','within_rounding','unexplained_residual',
                        'incomplete_input','missing_prior_checkpoint',
                        'ambiguous_transfer','not_applicable')),
    reason           TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                       CHECK (length(created_at) = 20
                              AND created_at GLOB '????-??-??T??:??:??Z')
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_scope
    ON reconciliation_results(account_id, current_checkpoint, kind, status);

CREATE TABLE IF NOT EXISTS reconciliation_components (
    reconciliation_id INTEGER NOT NULL REFERENCES reconciliation_results(reconciliation_id)
                                  ON DELETE CASCADE,
    transaction_id    INTEGER NOT NULL REFERENCES transactions(transaction_id)
                                  ON DELETE CASCADE,
    delta             REAL NOT NULL,
    PRIMARY KEY(reconciliation_id, transaction_id)
);

-- ---------------------------------------------------------------------------
-- META
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_meta (
    key              TEXT PRIMARY KEY,
    value            TEXT NOT NULL
);

INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', '8');

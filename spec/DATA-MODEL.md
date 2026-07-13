# Data model

This document describes the schema currently implemented. Exact SQLite DDL is
owned by `src/ledger/db/schema.sql`; exact DuckDB DDL is the `DDL` string in
`src/ledger/db/duckdb_store.py`. Update code and this spec together.

## SQLite ledger (schema version 5)

All dates are ISO text and monetary values remain in the row's native
`currency`.

| Area | Tables | Current identity/role |
|---|---|---|
| Brokers | `institutions`, `accounts` | institution code; `(institution_id, account_number)` |
| Transfers | `account_links` | automatically paired account-to-account transfers |
| Securities | `instruments`, `instrument_aliases`, `instrument_identifier_lookups` | parsed/reviewed security identities and aliases |
| Source | `source_files`, `statements` | one source path; statement unique on `(source_file_id, account_id, period_end)` |
| Ledger | `transactions`, `quarantine_transactions` | movements and unparsed evidence |
| Checkpoints | `position_snapshots`, `cash_balances` | statement holdings/cash by date |
| Pre-history | `initial_positions`, `initial_cash` | user-curated or tagged inferred anchors |
| Reports | `annual_performance_reports` | annual statement totals/returns, not movements |
| Attribution | `position_transaction_links` | transactions since the prior same-ID snapshot |
| Metadata | `schema_meta` | current schema version |

### Transactions

`transactions` stores account/source/statement, trade and optional settlement
date, `txn_type`, optional instrument, quantity, price, gross/fees/net amount,
currency, optional transfer counterpart/tax data, description, `raw_line`, and
parser confidence. The database does not constrain `txn_type`; the Python
literal vocabulary is defined in `parsers/types.py`.

`quantity` is not consistently normalized by all parsers. Consumers call
`quantity.quantity_delta()` to interpret position effect. `net_amount` is
intended to be signed cash effect, but the parser corpus does not currently
satisfy that contract reliably.

### Checkpoints

`position_snapshots` is unique on `(statement_id, instrument_id)` and stores
reported quantity/cost/price/value/P&L plus optional raw text. `cash_balances`
is unique on `(statement_id, currency)` and stores opening/closing values, but
no source raw line. Neither table represents section scope or completeness.

### Instrument identity defect

The current uniqueness key is:

```text
(asset_type, symbol, currency, option_expiry, option_strike, option_type)
```

Option fields are `NULL` for ordinary instruments. In SQLite, `NULL` values do
not conflict under a unique constraint, so repeated ordinary-instrument
upserts create new rows. Transactions and snapshots for the same printed
security can therefore reference different IDs. This is a confirmed defect,
not intended behavior.

### Statement identity defect

The statement key omits `period_start` and `statement_type`. More importantly,
the current parser type cannot represent child currency/section scopes. RBC
CAD and USD outputs share a key and overwrite each other's children. Snapshot
completeness is also absent.

## DuckDB market store

| Table | Primary key | Contents |
|---|---|---|
| `daily_prices` | `(symbol, trade_date)` | OHLC, adjusted close, volume, optional exchange/currency |
| `dividends` | `(symbol, ex_date)` | amount and currency |
| `splits` | `(symbol, split_date)` | split ratio |
| `option_implied_vol` | `(symbol, trade_date)` | 30/60/90-day IV placeholders/data |
| `fx_rates` | `(base, quote, rate_date)` | dated conversion rate |
| `symbol_profiles` | `symbol` | name, sector, industry, quote type, fetch time |
| `financials_quarterly` | `(symbol, period_end)` | fiscal metadata and statement metrics |
| `financials_annual` | `(symbol, period_end)` | annual statement metrics |
| `earnings_events` | `(symbol, report_date)` | estimates/actuals and surprise |
| `scrape_log` | none | provider attempt audit rows |

The market store is rebuildable and must not contain private account data.
Price identity is currently symbol/date only; exchange/currency are not part of
the primary key.

## Current migration behavior

`db init` executes idempotent DDL and compatibility migrations in
`db/sqlite.py`; it is not a general revisioned migration framework. Any schema
refactor must include tests from a pre-refactor database and must build a
shadow database before live cutover.

## Target direction (not implemented)

The approved plan adds a non-null canonical `instrument_key`, unambiguous
statement keys, child snapshot scopes with completeness, source spans,
versioned ingestion runs, normalized position/cash deltas, and explicit
reconciliation results. See the plan for sequencing; do not code as if these
columns already exist.

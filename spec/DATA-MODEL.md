# Data model

This document describes the schema currently implemented. Exact SQLite DDL is
owned by `src/ledger/db/schema.sql`; exact DuckDB DDL is the `DDL` string in
`src/ledger/db/duckdb_store.py`. Update code and this spec together.

## SQLite ledger (schema version 6)

All dates are ISO text and monetary values remain in the row's native
`currency`.

| Area | Tables | Current identity/role |
|---|---|---|
| Brokers | `institutions`, `accounts` | institution code; `(institution_id, account_number)` |
| Transfers | `account_links` | automatically paired account-to-account transfers |
| Securities | `instruments`, `instrument_aliases`, `instrument_identifier_lookups` | one non-null canonical key per logical instrument plus reviewed aliases |
| Source | `source_files`, `ingestion_runs`, `statements` | source metadata, immutable attempts, and account-period output |
| Evidence | `source_evidence` | deterministic source-row provenance without exposing it in public audit logs |
| Ledger | `transactions`, `quarantine_transactions` | reported rows plus normalized deltas and evidence links |
| Checkpoints | `snapshot_sets`, `position_snapshots`, `cash_balances` | independently complete currency/section checkpoints |
| Pre-history | `initial_positions`, `initial_cash` | user-curated or tagged inferred anchors |
| Reports | `annual_performance_reports` | annual statement totals/returns, not movements |
| Reconciliation | `position_transaction_links`, `reconciliation_results`, `reconciliation_components` | legacy attribution plus explicit result/equation storage |
| Metadata | `schema_meta` | current schema version |

### Canonical instrument identity

`instruments.instrument_key` is non-null and unique. The shared normalizer in
`identity.py` produces `ik1` keys from asset type, normalized symbol, and
native currency; option keys additionally include root, expiry, strike, type,
and multiplier. `upsert_instrument()` conflicts only on this key, never on
nullable option columns.

The v5-to-v6 migration repoints dependent rows to the oldest canonical ID. If
two duplicate legacy holding/initial rows collide, it preserves their total
reported quantity/value and marks no new source facts. The shadow rebuild in
the plan remains the authoritative repair/cutover route for live derived data.

### Statements, attempts, and evidence

`statements` is unique on
`(source_file_id, account_id, period_start, period_end, statement_type)` and
has a deterministic `sk1` `statement_key`. A statement belongs to an
`ingestion_run`; source metadata points at at most one active successful run.

The ingest pipeline creates a `validated` run, writes every child inside one
source savepoint, writes its deterministic `content_counts_json` and
`content_hash`, then switches `source_files.active_ingestion_run_id`. Failed
attempts retain their own run/status/error while leaving the previous active
pointer and active metadata intact. Successful replacements remove the prior
derived run and its source children in that transaction. Schema v6's global
statement/evidence uniqueness prevents old/new copies from coexisting, so this
replacement is uncommitted until activation; readers only see the prior or new
complete source output.

`source_evidence` has a deterministic non-content-revealing key, source/run,
row occurrence, raw text, optional page/line/coordinates/words, and parser
rule/version. New transactions, holdings, cash balances, and quarantine rows
link to evidence. Parser v2 rows carry page/line evidence and retain available
`pdfplumber` coordinates/words; text-only extraction uses no invented box.
Legacy migrated cash evidence explicitly has no raw source text rather than a
fabricated line.

### Transactions and normalized effects

`transactions.quantity` and amount fields retain the reported parser values.
`position_delta`, `cash_delta`, and `cash_effective_date` hold the normalized
effects used by new consumers; `net_amount` remains a compatibility field.
Resolution method/confidence and an optional resolution-evidence link are also
available. Phase 3 writes these through its conservative staged resolver:
explicit printed identities, reviewed aliases/fund lookups, and unambiguous
same-statement holdings are distinguishable from unresolved printed names. The
database does not constrain `txn_type`; the Python literal vocabulary and
validator own it.

### Scoped checkpoints

`snapshot_sets` declares a statement/account/date/currency/section scope with
`complete`, `partial`, `absent`, or `unknown` completeness. Position snapshots
are unique within `(snapshot_set_id, instrument_id)` and cash balances within a
cash snapshot set. `can_clear_omitted` is true only for a complete set.

Monthly and Performance now refuse to clear earlier holdings from partial or
unknown scopes. Parser v2 explicitly declares a recognized holdings section
and a cash section with a valid closing balance as `complete`; incomplete or
unrecognized sections remain `unknown`. Existing migrated/live rows retain
their historical `unknown` scopes until a reviewed re-ingest or shadow rebuild.

### Reconciliation storage

`reconciliation_results` can store a position, cash, statement-total, or
transfer equation with checkpoints, deltas, expected/reported close, residual,
tolerance, status, and reason. `reconciliation_components` can point to the
contributing transactions. No reconciliation engine writes these records yet;
the existing command still builds transfer and attribution links only.

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

`db init` executes idempotent DDL and a tested v5-to-v6 compatibility
migration in `db/sqlite.py`. The migration preserves row IDs and foreign keys,
creates legacy source runs/evidence, and marks migrated snapshot scopes
`unknown`. It is not a replacement for the planned shadow rebuild; do not use
it as a live-data correctness cutover.

## Still pending

The remaining phases calculate and persist reconciliation results, unify all
holdings consumers, and rebuild/cut over a shadow ledger. See the plan for
sequencing.

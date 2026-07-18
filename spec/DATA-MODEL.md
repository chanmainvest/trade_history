# Data model

This document describes the schema currently implemented. Exact SQLite DDL is
owned by `src/ledger/db/schema.sql`; exact DuckDB DDL is the `DDL` string in
`src/ledger/db/duckdb_store.py`. Update code and this spec together.

## SQLite ledger (schema version 8)

SQLite has no dedicated date, datetime, or enum storage class. Business dates
therefore remain canonical `YYYY-MM-DD` `TEXT`, and audit timestamps use one
UTC representation: `YYYY-MM-DDTHH:MM:SSZ`. Schema-v8 checks/triggers reject
malformed or impossible new dates, non-canonical timestamps, currencies outside
the private-ledger CAD/USD domain, and malformed SHA-256 values. API date
parameters are parsed as dates before a query runs. Monetary values remain in
the row's native `currency`.

| Area | Tables | Current identity/role |
|---|---|---|
| Brokers | `institutions`, `accounts` | institution code; `(institution_id, account_number)` |
| Transfers | `account_links` | automatically paired account-to-account transfers |
| Securities | `instruments`, `instrument_aliases`, `instrument_identifier_lookups`, `instrument_ticker_changes`, `instrument_ticker_change_sources` | canonical listing identities, reviewed aliases, and dated ticker lineages |
| Source | `source_files`, `ingestion_runs`, `statements` | source metadata, immutable attempts, and account-period output |
| Evidence | `source_evidence` | deterministic semantic source-row provenance without exposing it in public audit logs |
| Geometry | `source_pages`, `source_lines`, `source_evidence_geometry`, `source_evidence_lines` | rebuildable PDF coordinates and evidence-to-line matches |
| Ledger | `transactions`, `quarantine_transactions` | reported rows plus normalized deltas and evidence links |
| Checkpoints | `snapshot_sets`, `position_snapshots`, `cash_balances` | independently complete currency/section checkpoints |
| Pre-history | `initial_positions`, `initial_cash` | user-curated or tagged inferred anchors |
| Reports | `annual_performance_reports` | annual statement totals/returns, not movements |
| Reconciliation | `position_transaction_links`, `reconciliation_results`, `reconciliation_components` | movement attribution plus generated, source-traceable checkpoint equations |
| Metadata | `schema_meta` | current schema version |

### Canonical instrument identity

`instruments.instrument_key` is non-null and unique. The shared normalizer in
`identity.py` produces `ik1` keys from asset type, normalized symbol, and
native currency; option keys additionally include root, expiry, strike, type,
and multiplier. `upsert_instrument()` conflicts only on this key, never on
nullable option columns.

A ticker change does **not** merge those keys. `instrument_ticker_changes`
links the old and new instrument IDs with an ISO effective date, positive
conversion ratio, resolution provenance, and a non-branching/non-cyclic
lineage contract. `instrument_ticker_change_sources` links every corroborating
statement transaction and its evidence. Extracted relationships are removed
only when their final source transaction is replaced; aliases remain reserved
for names that are equivalent without a date.

The v5-to-v6 migration repoints dependent rows to the oldest canonical ID. If
two duplicate legacy holding/initial rows collide, it preserves their total
reported quantity/value and marks no new source facts. The shadow rebuild in
the plan remains the authoritative repair/cutover route for live derived data.

### Account metadata and shadow transfer

`accounts` retains optional `nickname`, `opened_on`, `closed_on`, and `notes`
in addition to its broker identity/type/base currency. The shadow workflow
matches each account by `(institution code, account number)` and preserves its
numeric `account_id` in the otherwise fresh target, so the local portfolio
configuration's `account_ids` remain valid after a database-only cutover. An
ID collision or an unmapped configured account aborts/blocks normal sign-off
rather than silently remapping preferences. It separately transfers manual
(not `inferred:`) initial anchors, reviewed aliases/lookups, and non-generated
reconciliation annotations and reviewed ticker changes only when their
canonical target references can be mapped.

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
derived run and its source children in that transaction. Schema v8's global
statement/evidence uniqueness prevents old/new copies from coexisting, so this
replacement is uncommitted until activation; readers only see the prior or new
complete source output.

`source_evidence` has a deterministic non-content-revealing `ev2` key,
source/run, row occurrence, raw text, optional semantic page/line hint, and
parser rule/version. The key deliberately excludes coordinates and page/line,
so rerunning a geometry extractor cannot change semantic row identity. New
transactions, holdings, cash balances, and quarantine rows link to evidence.
Legacy single-box fields remain readable for compatibility.

The four geometry tables are derived after semantic extraction. They retain
page dimensions, normalized line hashes, exact boxes/words, an explicit match
status, and the lines linked to each evidence row. Geometry is replaceable and
is excluded from the semantic ingestion content hash. Repeated text without a
unique persisted hint is `ambiguous`, not assigned by row order. Legacy
migrated cash evidence explicitly has no raw source text rather than a
fabricated line.

`sha256`, `source_sha256`, and content/line hashes stay lowercase 64-character
hex `TEXT`: this is readable, interoperable with Python tooling, and now
validated as hash-shaped data. `schema_version` is an integer because it is a
monotonic database format number. Parser, parser-contract, resolver, and
geometry-extractor versions remain `TEXT` because they are semantic versions
or named/fingerprinted algorithms, not quantities that support arithmetic.

### Transactions and normalized effects

`transactions.quantity` and amount fields retain the reported parser values.
`position_delta`, `cash_delta`, and `cash_effective_date` hold the normalized
effects used by new consumers; `net_amount` remains a compatibility field.
When a generic split, name change, spinoff, or merger has no explicit safe
position effect, `position_delta` remains null rather than being fabricated as
zero.
Resolution method/confidence and an optional resolution-evidence link are also
available. Phase 3 writes these through its conservative staged resolver:
explicit printed identities, reviewed aliases/fund lookups, and unambiguous
same-statement holdings are distinguishable from unresolved printed names. The
database does not constrain `txn_type`; the Python literal vocabulary and
validator own it.

Post-ingest reconciliation may replace a null `instrument_id` on an unresolved
buy/sell with a canonical holding identity already observed in the same native
currency. Methods `account_holding_name` and `portfolio_holding_name` identify
these rebuildable derived links; `resolution_evidence_id` points at the
supporting position row. The description and reported numeric fields remain
unchanged. Existing transaction and checkpoint evidence is sufficient for this
derivation, while `instrument_aliases` remains reserved for reviewed mappings.

Parser-contract v4 includes v3's `related_instrument` and
`corporate_action_ratio` support and makes semantic evidence identity
independent of replaceable geometry. For a supported ticker change,
`instrument_id` is the old printed
listing and the related instrument is persisted through the dated relationship.
Both must have the same asset type/native currency and different symbols.

### Scoped checkpoints

`snapshot_sets` declares a statement/account/date/currency/section scope with
`complete`, `partial`, `absent`, or `unknown` completeness. Position snapshots
are unique within `(snapshot_set_id, instrument_id)` and cash balances within a
cash snapshot set. `can_clear_omitted` is true only for a complete set.

The canonical holdings service uses `snapshot_sets` as read-only anchors for
Monthly, Performance, and Visualisations. It clears omitted rows only from a
complete scope; a newer partial/unknown scope leaves the prior anchor intact
and returns a quality warning. Parser v2 explicitly declares a recognized
holdings section and a cash section with a valid closing balance as `complete`;
incomplete or unrecognized sections remain `unknown`. Existing migrated/live
rows retain their historical `unknown` scopes until a reviewed re-ingest or
shadow rebuild.

### Reconciliation storage

`reconciliation_results` stores a position, cash, statement-total, or transfer
equation with checkpoints, deltas, expected/reported close, residual,
tolerance, status, and reason. `reconciliation_components` identifies the
contributing transaction rows and their signed contribution.

`rebuild_reconciliation_results()` writes only generated keys prefixed
`recon:v1:`. It replaces that generated subset on every run, retaining any
reviewed/manual result that uses another key. The engine runs after an ingest
scan and through `ledger ingest reconcile`; it calculates scoped position
roll-forwards, direct and adjacent cash equations, and printed section or
portfolio totals. Results point to snapshot sets/statements, while components
point to evidence-linked transactions. No result creates an adjustment row.

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

`db init` executes idempotent DDL and a tested v5-to-v6 compatibility migration
in `db/sqlite.py`, then installs v8 domain triggers on pre-v8 tables. The
migration preserves row IDs and foreign keys, creates legacy source
runs/evidence, and marks migrated snapshot scopes `unknown`. Historical values
are not rewritten merely to normalize formatting; new or changed domain values
are checked. It is not a replacement for the planned shadow rebuild; do not use
it as a live-data correctness cutover.

## Still pending

The GUI surfaces reconciliation/holdings quality read-only. A shadow ledger can
be built and compared safely, but human source review and explicit cutover
remain operational gates. See the plan for sequencing.

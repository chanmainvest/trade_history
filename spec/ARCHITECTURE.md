# Architecture

This page is the concise system map. Detailed contracts live in the focused
specifications linked from [INDEX.md](INDEX.md).

## System boundary

```text
read-only PDFs under Statements/
        |
        v
pdfplumber -> pypdf fallback -> institution parser -> SQLite ledger
                                                       |
yfinance + SEC Company Facts ---------------------> DuckDB market data
                                                       |
                                          FastAPI read/query layer
                                                       |
                                               React/Vite GUI
```

Statement ingestion, symbol repair, initial inference, and reconciliation are
CLI operations. The HTTP statement routes only list/serve existing statements
and extraction boxes. `PUT /config` writes user preferences to JSON; it does
not mutate the ledger.

## Runtime packages

```text
src/ledger/
  config.py                 workspace paths and institution folders
  cli.py                    Click command tree
  pdf_text.py               raw PDF text, optional word/line geometry, fingerprinting
  quantity.py               transaction-type quantity movement rules
  ticker_changes.py         explicit ticker-pair parsing, persistence, lineage queries
  instrument_catalog.py     reviewed broker-name/listing/provider identities
  holdings.py               canonical read-only scoped holdings reconstruction
  shadow.py                 read-only source export, staged rebuild, compare, guarded cutover
  db/
    schema.sql              canonical SQLite DDL
    sqlite.py               connections, initialization, upserts
    duckdb_store.py          canonical market-data DDL
  parsers/                  common types, layout/provenance bridge, four bank parsers
  ingest/
    pipeline.py             discovery, validate, staged source activation, audit export
    layout_enrichment.py    replaceable PDF geometry extraction and evidence linking
    identity_resolution.py  conservative in-stage printed/alias/holding resolution
    instrument_resolution.py catalog repair and candidate status
    yahoo_resolution.py     opt-in public-name/provider verification
    repair_symbols.py       legacy/manual post-parse identity repair
    fund_lookup.py          reviewed fund-code lookup workflow
    initials.py             inferred pre-history anchors
    reconcile.py            transfer pairing, movement links, and scoped checkpoint equations
  market/                   prices, actions, profiles, financials, FX
  api/
    app.py                  FastAPI application and route registration
    routes/                 transactions/monthly/performance/research/viz/config/statements

frontend/src/
  App.tsx                   tab routing and global controls
  portfolio.tsx             preferences and account portfolios
  tabs/                     seven current screens
```

There is no `src/ledger/analytics/` package. Analytics queries currently live
inside the API route modules.

## Storage split

- SQLite (`<DATA_DIR>/ledger.sqlite`) contains private account, statement,
  transaction, checkpoint, quarantine, reconciliation-link, and reconciliation-result data.
- DuckDB (`<DATA_DIR>/market.duckdb`) contains replaceable public price,
  corporate-action, profile, financial, earnings, and FX data.
- `data/config.json` contains UI preferences and named account portfolios.
- `Statements/` is immutable input. `logs/` and text dumps are derived local
  artifacts.

See [DATA-MODEL.md](DATA-MODEL.md) for persisted contracts.

## Core invariants

- Preserve native currency in the ledger; convert only for a requested view.
- Treat a proven-complete broker snapshot as a quantity/cash checkpoint.
- Keep transactions as movements/audit evidence, not as a substitute for a
  missing checkpoint.
- Preserve uncertainty instead of manufacturing values.
- Keep every derived row traceable to its source statement.
- Preserve old/new ticker listings as dated identities rather than aliases.
- Keep issuer, security/share class, broker listing, and provider symbol as
  separate identities; permit cross-currency journals only through an explicit
  pair.

The current implementation violates parts of the last three invariants. Those
violations are enumerated in [CURRENT-STATE.md](CURRENT-STATE.md), not hidden
as completed behavior.

## Main read paths

- Transactions directly query normalized ledger rows.
- `holdings.py` selects complete scoped checkpoints, replays normalized
  movements, prices reconstructed quantities, and returns provenance/quality.
- Monthly, Performance, and Visualisations consume that same holdings service;
  Research stitches each dated ticker lineage across SQLite trades and DuckDB
  prices/financials.
- Verify serves the immutable original PDF, renders only statement-owned
  physical pages, and reads persisted evidence-specific geometry. It never
  fuzzy-matches financial rows during an API request.

The GUI renders holdings quality/reconciliation warnings; see
[RECONCILIATION.md](RECONCILIATION.md).

## Detailed context

- Extraction and activation: [INGESTION.md](INGESTION.md)
- Parser data contract: [PARSER-CONTRACT.md](PARSER-CONTRACT.md)
- Reconciliation/read models: [RECONCILIATION.md](RECONCILIATION.md)
- API and tabs: [API-UI.md](API-UI.md)
- Commands and deployment: [OPERATIONS.md](OPERATIONS.md)

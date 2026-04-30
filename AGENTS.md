# Ledger (`trade_history_opus47`) — Agent Notes

This is a **fresh** rebuild. Do **not** copy code from `trade_history/` or
`trade_history_opus46/`; they are abandoned attempts. Reference them only as
documentation of what *not* to do.

---

## Tech Stack

- **Backend**: Python 3.12+, FastAPI, click CLI.
- **DBs**:
  - SQLite (private) → transactions, positions, statements, cash.
  - DuckDB (public market data) → prices, dividends, splits, financials, FX.
- **PDF extraction**: `pdfplumber` (primary) → `pypdf` (fallback). No OCR.
- **Frontend**: React + Vite + TypeScript + Plotly.js. State via React Query.
- **Tooling**: `uv` for all Python; `npm` for the frontend.

## Tooling Rules

- ALWAYS use `uv run …` for Python commands. Never bare `python` or `pip`.
- Frontend must build via `npm run build` before any commit that touches `frontend/src/`.
- All scripts log to `logs/<name>.log`. Structured logs use `<name>.jsonl`.
- Statements PDFs are read-only inputs. Never move, rename, or delete them.

## Repository Layout

```
src/ledger/
  config.py            paths + institution map
  logging_setup.py     get_logger(name) → file + stdout handlers
  pdf_text.py          pdfplumber→pypdf, returns PdfText(pages, sha256, …)
  cli.py               `ledger` entry point (db, pdf, ingest, market, serve)
  db/
    schema.sql         SQLite DDL (single source of truth)
    sqlite.py          connect/init + upsert helpers
    duckdb_store.py    DuckDB DDL + connect helpers
  parsers/
    types.py           ParsedStatement / ParsedTxn / ParsedPosition / …
    helpers.py         parse_money, parse_date, parse_option_expiry
    registry.py        register() / select_parser()
    cibc_is/           CIBC Imperial Service
    cibc_id/           CIBC Investor's Edge ("Invest Direct")
    cibc_tfsa/         CIBC TFSA
    hsbc/              HSBC Direct Invest
    rbc/               RBC Direct Investing
    td/                TD WebBroker
  ingest/
    pipeline.py        walk Statements/, run parser, write to SQLite
  market/
    scrape.py          yfinance + HTTP scrape into DuckDB (rate limited)
  analytics/           positions/PnL/RRG/correlation/treemap
  api/
    app.py             FastAPI factory
    routes/            /transactions /monthly /performance /research /viz
```

## SQLite Schema (Source of Truth)

See [src/ledger/db/schema.sql](src/ledger/db/schema.sql). Key principles:

1. **Multi-currency native.** Every monetary column is paired with a
   `currency` column. Cash is tracked per `(account, currency)`.
2. **Options first-class.** `instruments.asset_type` distinguishes
   `equity / etf / option / mutual_fund / bond / cash / other`. Option rows
   carry `option_root / option_expiry / option_strike / option_type /
   option_multiplier`. The unique key includes the option fields so the same
   underlying with different strikes/expiries is not collapsed.
3. **Transactions vocabulary** is fixed and documented in
   `parsers/types.py` (`TxnType`). Every parser must emit one of those literals.
4. **Statements ↔ source files**: `source_files` is per-PDF;
   `statements(source_file_id, account_id, period_end)` is the per-account
   period snapshot. A multi-account or multi-period PDF yields multiple
   `statements` rows referencing one `source_files` row.
5. **Position snapshots** (`position_snapshots`, `cash_balances`) come from
   the holdings table at the end of each statement. They are the
   ground-truth checkpoint; transactions are reconciled *to* them.
6. **Initial positions** (`initial_positions`, `initial_cash`) cover periods
   where transactions exist but no statement does (older trades before the
   first available statement).
7. **In-kind transfers between MY accounts** are linked via `account_links`
   (date-level) and pairwise via `transactions.counterpart_account_id` /
   `counterpart_txn_id` (event-level). This is what enables tracking a
   holding as it moves between accounts (e.g. CIBC ID → CIBC TFSA).
8. **Quarantine, never fabricate.** Any unparseable line goes to
   `quarantine_transactions` with `raw_line` + reason. Confidence < 1.0 is
   allowed in `transactions.parser_confidence` but the row must still be
   defensible against the PDF.

## DuckDB Schema

See [src/ledger/db/duckdb_store.py](src/ledger/db/duckdb_store.py). Tables:
`daily_prices`, `dividends`, `splits`, `option_implied_vol`, `fx_rates`,
`financials_quarterly`, `financials_annual`, `earnings_events`,
`scrape_log`. Each has a `PRIMARY KEY` natural key so re-scrapes are
idempotent.

## Parser Contract

```python
class Parser(Protocol):
    NAME: str
    VERSION: str
    def can_handle(self, folder_name: str, first_page_text: str) -> bool: ...
    def parse(self, pdf: PdfText) -> ParseResult: ...
```

- `select_parser` first checks the folder name, then sniffs first-page text.
- Parsers must be **deterministic** and **side-effect free**. They emit
  dataclasses defined in `parsers/types.py`. The ingest pipeline owns all
  DB writes.
- A parser MUST handle multi-account and multi-period PDFs; it returns
  `list[ParsedStatement]` inside `ParseResult`.

### Per-institution format notes

| Institution | Folder | Format quirks |
|---|---|---|
| CIBC Imperial Service | `CIBC Imperial Service/` | Monthly. Account # often only in filename. |
| CIBC Investor's Edge | `CIBC Invest Direct/` | Monthly. `DISTRIBUTION` = dividend. Option expiry `MM/DD/YY`. |
| CIBC TFSA | `CIBC TSFA/` | Same engine as CIBC IE. |
| HSBC Direct Invest | `HSBC direct invest/` | Compact options like `PUT-100TLT'2616JA@75`. Multiple sub-accounts in some PDFs. |
| RBC Direct Investing | `RBC Invest Direct/` | Full month names. Trailing `-` negatives. `BOUGHT/SOLD` keywords. Annual reports separate. |
| TD WebBroker | `TD Webbroker/` | Abbreviated month + day. `Statement-<acct> YYYY-MM-DD.pdf` plus legacy `<acct>-YYYY<MMM>dd-…pdf` filenames. |

## Currency & FX

- Every monetary amount carries `currency`. Cash balances per `(account, currency)`.
- FX conversions are presentation-only; ingest never converts.
- Daily FX rates are cached in DuckDB (`fx_rates`) and used by the
  performance tab when the user picks a display currency.

## Logs

- `logs/ingest.log` — main ingest run.
- `logs/parser_<institution>.log` — per-parser detail.
- `logs/skipped_pdfs.log` — image-only PDFs we skip.
- `logs/quarantine.jsonl` — same content as `quarantine_transactions`.
- `logs/market_scrape.log` + `logs/market_scrape.jsonl` — HTTP fetches,
  rate-limit hits, retries.

## Frontend Tabs

1. **Transactions** — virtualised table; filters by date range, account,
   institution, ticker, txn-type. Symbol cells link to tab 4.
2. **Monthly snapshot** — month-end picker; consolidated holdings + diff
   between any two months.
3. **Performance** — total asset value over time, with the same filter set
   as tab 1. Realized vs unrealized P&L.
4. **Stock research** — candlestick (default) with 50/200 MA toggles,
   volume sub-chart, my trade markers; daily/weekly/monthly resampling;
   below it, multi-line financials chart with per-metric show/hide.
5. **Visualisations** — drop-down: RRG, treemap by sector, correlation
   matrix (collapsible to sector). All animated by a date scrubber + play
   button.

## Required Validation After Changes

- `uv run pytest -q`
- `uv run ruff check src tests`
- `cd frontend && npm run build`
- Spot-check parser output against the corresponding PDF; every reported
  transaction must be defensible against the source.

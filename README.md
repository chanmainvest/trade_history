# Trade History and Performance Dashboard

This project ingests brokerage PDF statements, normalizes trading activity, links cross-account transfers, computes average-cost closed P/L, stores market/FX data, and serves a FastAPI + React dashboard.

## Scope Implemented

- Institutions in `Statements/`:
  - `CIBC Invest Direct`
  - `CIBC Imperial Service`
  - `CIBC TSFA`
  - `HSBC direct invest`
  - `RBC Invest Direct`
  - `TD Webbroker`
- Trade/cash event extraction framework with parser version tagging by statement era.
- Transfer continuity matching (`TRANSFER_OUT` -> `TRANSFER_IN`) across accounts.
- Average-cost closed-position realization for long/short activity.
- SQLite operational DB and DuckDB market/FX DB.
- Dual raw price ingestion (Stooq + Yahoo), cross-reference, and canonical series.
- BoC FX ingestion (`USD/CAD`) mirrored into SQLite for API valuation queries.
- FastAPI endpoints for:
  - trades table
  - closed P/L
  - asset value (total/account/institution)
  - sector allocation
  - symbol catalog and override admin
  - ingestion jobs
- React dashboard tabs with:
  - sortable/filterable trades table
  - asset value tab with separate `Stocks` and `Options` sections per group
  - sector tab
  - symbol admin tab (manual market-symbol and sector overrides)
  - CAD/USD toggle
  - privacy mode
  - English + Traditional Chinese
  - account grouping shown as `Institution | Account`

## Project Layout

```
src/trade_history/
  api/            # FastAPI app + routes
  core/           # Position/transfer reconciliation
  db/             # SQLite and DuckDB schemas
  ingest/         # Statement, price, FX pipelines
  parsers/        # Institution parsers and shared extraction
  services/       # API-facing analytics/job services
frontend/         # React + Vite UI
scripts/          # Per-institution extraction scripts + scheduler helpers
Statements/       # Source PDF statements
```

## Setup

1. Initialize Python environment and install dependencies:

```powershell
uv sync --extra dev
```

2. Initialize databases:

```powershell
uv run trade-history init-db
```

3. Run ingestion:

```powershell
uv run trade-history ingest statements
uv run trade-history ingest prices --sources stooq,yahoo
uv run trade-history ingest fx
uv run trade-history rebuild-views
```

If parser logic changes and you need to reparse unchanged PDFs, force re-ingestion:

```powershell
uv run trade-history ingest statements --force
```

4. Start backend:

```powershell
uv run trade-history serve --host 127.0.0.1 --port 8000
```

5. Start frontend:

```powershell
cd frontend
npm install
npm run dev
```

## Extraction Scripts

- `scripts/extract_cibc.ps1`
- `scripts/extract_hsbc.ps1`
- `scripts/extract_rbc.ps1`
- `scripts/extract_td.ps1`
- `scripts/run_daily_refresh.ps1`
- `scripts/create_daily_refresh_task.ps1`
- `scripts/export_statement_lines.py`
- `scripts/normalize_gemini_override.py`
- `scripts/gemini_extract_samples.sh`

## Gemini-Assisted Extraction Tuning

Use Gemini to label a small PDF sample set from each institution and feed corrections back into the parser.

1. Generate sample override files (Git Bash/WSL):

```bash
GEMINI_MODEL=gemini-2.5-flash SAMPLES_PER_INST=2 bash scripts/gemini_extract_samples.sh Statements data/gemini_overrides
```

2. Re-ingest with force so overrides are applied:

```powershell
uv run trade-history ingest statements --force
```

Override files are resolved per statement at:

- `data/gemini_overrides/<Institution Folder>/<StatementFileStem>.json`

The parser uses `source_line_ref` (`p#:l#`) to patch symbol and contract fields before insert.

## API Endpoints

- `GET /api/trades`
- `GET /api/positions/closed-pl`
- `GET /api/assets/value`
- `GET /api/assets/sector`
- `GET /api/meta/accounts`
- `GET /api/meta/auth-context`
- `GET /api/symbols`
- `GET /api/symbols/overrides`
- `PUT /api/symbols/overrides/{symbol_norm}`
- `DELETE /api/symbols/overrides/{symbol_norm}`
- `POST /api/symbols/refresh-sectors`
- `POST /api/jobs/ingest/statements`
- `POST /api/jobs/ingest/prices`
- `POST /api/jobs/ingest/fx`
- `POST /api/jobs/rebuild/views`

## Recent Fixes (Code Audit — July 2025)

A comprehensive audit identified and fixed several correctness issues across analytics, parsers, and schema:

### Analytics (`services/analytics.py`)

| Fix | Detail |
|-----|--------|
| Option multiplier in market value | `asset_values()` now uses `quantity * price * COALESCE(multiplier, 1)` instead of ignoring the contract multiplier. |
| Reconciliation formula | `monthly_statement_reconciliation()` computes derived closing as `opening + net_cash - fees` (was double-counting fees). |
| Currency P&L | Unrealized P&L converts market value to the instrument's native currency before subtracting native cost basis (was mixing CAD/USD). |

### Parsers (`parsers/common.py`, `parsers/cibc.py`)

| Fix | Detail |
|-----|--------|
| RBC full-month dates | `DATE_RE` now matches full month names (`JANUARY`–`DECEMBER`) alongside abbreviations. `DATE_FORMATS` includes `%B %d %Y`, `%B %d, %Y`, and `%B %d`. |
| Trailing-hyphen negatives | `parse_money()` handles RBC-style `32,870.00-` negative amounts. |
| Action vocabulary | `ACTION_RE` and `SIDE_MAP` recognize `BOUGHT` (→ BUY) and `DISTRIBUTION` (→ DIVIDEND). |
| Dividend instrument | Dividend events preserve the current instrument context; only interest/fee clears it. |
| TD option regex | Rewritten for `CALL-100 AAPL'25 17JA@225` with optional day and optional `@strike`. |
| HSBC option regex | Rewritten for compact `PUT-100TLT'2616JA@75` format. |
| CIBC option expiry | Accepts `MM/DD/YY` format in addition to `YYYY-MM-DD`. |
| CIBC phantom account | `CIBCImperialServiceParser._fallback_account_id()` no longer generates time-dependent `CIBCIS-YYYYMM`; searches the filename for a `\d{3}[-]?\d{5}` pattern instead. |

### Database Schema

| Fix | Detail |
|-----|--------|
| DuckDB unique constraints | All 6 DuckDB tables (`raw_stooq_daily`, `raw_yahoo_daily`, `canonical_prices`, `sector_map`, `boc_fx_daily`, `calendar_dim`) now carry `UNIQUE` constraints on their natural keys to prevent silent duplicates on re-ingestion. |
| SQLite cascade deletes | `events.source_file_id`, `statement_snapshots.source_file_id`, and `lot_closures.close_event_id` FKs now use `ON DELETE CASCADE`, so re-ingesting a file safely replaces old rows. |
| SQLite indexes | Added `idx_lot_closures_close_event` and `idx_lot_closures_account_instrument` for P&L query performance. |

## Notes

- Statement parsing is format-aware by file era and stores unresolved lines in `quarantine_transactions` for review.
- Symbol extraction now rejects common issuer/institution noise tokens (for example `CIBC`, `BANK`, `CANADIAN`) to avoid false symbol assignments.
- Optional Gemini sidecar overrides can patch event instrument fields by line reference, enabling targeted fixes without rewriting parser code for every statement variant.
- Account-ID extraction now rejects short numeric tokens (for example street numbers like `1234`) to avoid address lines being treated as account IDs.
- Database model supports both stocks and options in the same pipeline:
  - `instruments.asset_type` (`equity` / `option`)
  - option contract attributes (`option_root`, `strike`, `expiry`, `put_call`, `multiplier`)
  - `position_state` and asset valuation preserve contract-level positions for options.
- Canonical prices prioritize Stooq when both sources exist, while preserving raw source tables for audit.
- Yahoo endpoint can rate-limit (`429`) during large backfills; the pipeline retries with backoff and still keeps Stooq raw/canonical data available.
- Price symbol normalization uses a built-in alias map in `src/trade_history/ingest/market.py`. Extend this map as you refine issuer-name to ticker mappings.
- Institution-specific format conventions:
  - **RBC**: Full month names (`JUNE 28`), trailing-hyphen negatives (`32,870.00-`), action `BOUGHT`.
  - **TD**: `Mon DD` dates (`Jun 18`), option symbols `CALL-100 CNQ'25 JA@50`.
  - **HSBC**: Compact option symbols `PUT-100TLT'2616JA@75`.
  - **CIBC**: Option expiry may be `MM/DD/YY`; `DISTRIBUTION` action for dividends.
- OAuth adapter now supports OIDC JWT validation through JWKS:
  - `TH_AUTH_MODE=oauth`
  - `TH_AUTH_OAUTH_JWKS_URL=...`
  - `TH_AUTH_OAUTH_ISSUER=...` (optional)
  - `TH_AUTH_OAUTH_AUDIENCE=...` (optional)
  - `TH_AUTH_OAUTH_ALGORITHMS=RS256` (comma-separated if multiple)
  - `TH_AUTH_READ_SCOPE=trade_history.read`
  - `TH_AUTH_WRITE_SCOPE=trade_history.write`
- In local mode (`TH_AUTH_MODE=none`), auth is bypassed for development.

## Docker

Build and run with Docker Compose (backend + static frontend in one container):

```powershell
docker compose up --build
```

Open:

- API/docs: `http://localhost:8000/docs`
- Dashboard: `http://localhost:8000/`

By default compose mounts:

- `./Statements` -> `/data/Statements` (read-only)
- `./data` -> `/app/data`

Run ingestion inside container:

```powershell
docker compose run --rm trade-history uv run trade-history ingest statements
docker compose run --rm trade-history uv run trade-history ingest prices --sources stooq,yahoo
docker compose run --rm trade-history uv run trade-history ingest fx
docker compose run --rm trade-history uv run trade-history rebuild-views
```

## Validation Checks

Useful verification queries after statement ingestion:

```powershell
# Ensure street-address false positives are gone
uv run python -c "import sqlite3; c=sqlite3.connect('data/trading.sqlite'); print(c.execute(\"select count(*) from accounts where account_id='1234'\").fetchone()[0])"

# Confirm both equities and options exist in open positions
uv run python -c "import sqlite3; c=sqlite3.connect('data/trading.sqlite'); [print(r) for r in c.execute(\"select i.asset_type, count(*) from position_state p join instruments i on i.instrument_id=p.instrument_id group by i.asset_type\")]"
```

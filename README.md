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

## Notes

- Statement parsing is format-aware by file era and stores unresolved lines in `quarantine_transactions` for review.
- Account-ID extraction now rejects short numeric tokens (for example street numbers like `1234`) to avoid address lines being treated as account IDs.
- Database model supports both stocks and options in the same pipeline:
  - `instruments.asset_type` (`equity` / `option`)
  - option contract attributes (`option_root`, `strike`, `expiry`, `put_call`, `multiplier`)
  - `position_state` and asset valuation preserve contract-level positions for options.
- Canonical prices prioritize Stooq when both sources exist, while preserving raw source tables for audit.
- Yahoo endpoint can rate-limit (`429`) during large backfills; the pipeline retries with backoff and still keeps Stooq raw/canonical data available.
- Price symbol normalization uses a built-in alias map in `src/trade_history/ingest/market.py`. Extend this map as you refine issuer-name to ticker mappings.
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

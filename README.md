# Trade History

Trade History is a local, multi-broker investment ledger for CIBC, HSBC, RBC,
and TD statements. It extracts read-only PDFs into a native-currency SQLite
ledger, combines them with public market data in DuckDB, and exposes a FastAPI
backend plus a React dashboard.

> **Data-quality status (2026-07-12):** the app and GUI run, but the current
> extraction, instrument identity, and month-end reconciliation paths have
> confirmed correctness defects. Do not treat the current database as fully
> reconciled. See [Current state](spec/CURRENT-STATE.md) and the
> [implementation plan](plan/EXTRACTION_RECONCILIATION_REFACTOR.md).

## Architecture at a glance

```text
Statements/*.pdf (read-only)
  -> pdfplumber/pypdf -> institution parsers -> data/ledger.sqlite

yfinance / SEC Company Facts -> data/market.duckdb

SQLite + DuckDB -> FastAPI -> React/Vite GUI
```

Private account/statement data stays in SQLite. DuckDB contains rebuildable
public market data. The browser queries the ledger but does not ingest or
reconcile it; those operations remain CLI-only. The UI can update local
preferences in `data/config.json`.

## Quick start

Requirements: Python 3.12+, `uv`, Node.js, and npm.

```powershell
uv sync --all-extras --dev
uv run ledger db init

# backend
uv run ledger serve --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5175 --strictPort
```

Open `http://127.0.0.1:5175`. On shared machines, verify the backend title at
`http://127.0.0.1:8000/openapi.json` before assuming a port belongs to this
project.

To use the synthetic workspace instead of private data, set the profile before
starting Python:

```powershell
$env:LEDGER_PROFILE = "example"
uv run python scripts/build_example_data.py
```

## Common commands

```powershell
# parse statement PDFs (current extraction limitations apply)
uv run ledger ingest run

# force parser code changes to be applied to unchanged PDFs
uv run ledger ingest run --force

# post-ingest maintenance
uv run ledger ingest repair-symbols
uv run ledger ingest reconcile
uv run ledger ingest infer-initials

# read-only parser/contract audit (does not open SQLite)
uv run ledger audit extraction --statements-dir Statements

# public market data
uv run ledger market refresh-all

# validation
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

`ingest reconcile` currently pairs transfers and builds movement-attribution
links; it does **not** calculate or persist month-end residuals. See
[Reconciliation](spec/RECONCILIATION.md).

## Docker

```powershell
docker compose up --build
```

The backend is exposed on 8000 and the production frontend on 5173. Compose
mounts local `data/`, `logs/`, and read-only-input `Statements/` paths into the
backend container (the current compose mount itself is not filesystem
read-only, so the application safety rule still applies).

## GUI

The current tabs are Transactions, Monthly, Performance, Research,
Visualisations, Verify extraction, and Settings. Verify renders the original
PDF beside parsed rows and highlights fuzzy-matched text-line boxes. Settings
manages named account portfolios; theme, language, and hide-money controls are
in the top bar.

Known limitations are shown in [Current state](spec/CURRENT-STATE.md). In
particular, Monthly, Performance, and Visualisations do not yet share one
canonical holdings engine.

## Repository map

```text
src/ledger/          backend, parsers, ingestion, storage, market, API
frontend/src/        React application and tabs
spec/                focused on-demand technical and user documentation
plan/                approved refactor plan
tests/               Python tests (fixture self-containment is in progress)
scripts/             docs/example-data and local diagnostic helpers
docs/index.html      generated documentation site
example_data/        synthetic profile
Statements/          private read-only PDF inputs (not committed)
data/                private local databases/config (not committed)
```

Start technical reading at [spec/INDEX.md](spec/INDEX.md). Human setup and tab
details are in the [User guide](spec/USER-GUIDE.md). Agent rules are in
[AGENTS.md](AGENTS.md).

## Data-quality rule

Never invent missing values, silently turn failed parses into zero, or create a
balancing transaction. Preserve native currency and quarantine uncertainty
with source evidence. Statement PDFs must never be modified, renamed, moved, or
deleted by the application or an agent.

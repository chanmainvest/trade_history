# Trade History

Trade History is a local, multi-broker investment ledger for CIBC, HSBC, RBC,
and TD statements. It extracts read-only PDFs into a native-currency SQLite
ledger, combines them with public market data in DuckDB, and exposes a FastAPI
backend plus a React dashboard.

> **Data-quality status (2026-07-18):** the app and GUI run, the CLI persists
> scoped month-end reconciliation results, and a parser-v2 shadow ledger has
> passed a double-build comparison. The live database has not been cut over, so
> do not treat it as fully reconciled. See [Current state](spec/CURRENT-STATE.md)
> and the [implementation plan](plan/EXTRACTION_RECONCILIATION_REFACTOR.md).

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
uv run ledger serve --host 0.0.0.0 --port 8000
```

For an existing real ledger, use the guarded shadow workflow rather than
upgrading the live database in place:

```powershell
uv run ledger shadow build
```

It performs two clean target rebuilds and writes a redacted comparison report;
human PDF review and the separate cutover command remain required. See
[Operations](spec/OPERATIONS.md) and the plan.

In another terminal:

```powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173` on this computer or
`http://<computer-LAN-IP>:5173` on another trusted LAN device. On shared
machines, verify the backend title at `http://127.0.0.1:8000/openapi.json`
before assuming a port belongs to this project.

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

# deliberately bypass the contract-aware ingest cache
uv run ledger ingest run --force

# legacy/manual derived-data repair, then derived reconciliation rebuild
uv run ledger ingest repair-symbols
uv run ledger ingest reconcile
uv run ledger ingest infer-initials

# safely rebuild and compare a non-live shadow ledger
uv run ledger shadow build

# read-only parser/contract audit (does not open SQLite)
uv run ledger audit extraction --statements-dir Statements

# public market data
uv run ledger market refresh-all

# validation
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

`ingest reconcile` first resolves uniquely supported name-only buy/sell rows
from observed same-currency holdings. It then pairs conservative transfer
candidates, rebuilds position-to-transaction links, and persists position,
cash, and printed-total equations. Ambiguous names remain unresolved; the
command reports residuals and incomplete inputs but never creates a balancing
row. See [Reconciliation](spec/RECONCILIATION.md).

Explicit statement ticker changes are stored as dated old-to-new instrument
relationships, not undated aliases. Reconciliation and holdings move the
position across that relationship, while Research joins the valid price,
trade, and financial history for either ticker.

Broker names, exchange listings, underlying securities/share classes, and
Yahoo symbols are stored separately. Run
`uv run ledger ingest resolve-instruments --verify-yahoo` to resolve queued
public security names and verify non-empty Yahoo history. Ambiguous names stay
unresolved for review. Cross-currency journals such as DLR/DLR.U match only through an
explicit fungible-security pair.

## Docker deployment

```powershell
docker compose up --build
```

Docker is for deployment/testing the deployment image, not the normal local
development loop. Run the FastAPI and Vite development servers directly on the
host as shown above.

The backend is exposed on 8000 and the production frontend on 5173. Compose
mounts local `data/`, `logs/`, and read-only-input `Statements/` paths into the
backend container (the current compose mount itself is not filesystem
read-only, so the application safety rule still applies).

## GUI

The current tabs are Transactions, Monthly, Performance, Research,
Visualisations, Verify extraction, and Settings. Verify renders the original
PDF pages owned by the selected logical statement beside parsed rows and
highlights persisted evidence rectangles. Parsed financial rows come first;
structured scope blockers, reconciliation, quarantine, and diagnostics follow.
Run `uv run ledger ingest enrich-layout` after semantic ingest to build those
replaceable coordinates; ambiguous or unmatched evidence stays visibly
unlinked instead of being fuzzy-matched. Enabled source icons are backed by a
server-confirmed drawable rectangle.
Monthly shows reported/reconstructed/incomplete holding state, checkpoint date,
quality warnings, and dated FX conversion details. Settings manages named
account portfolios; theme, language, and hide-money controls are in the top
bar.

Known limitations are shown in [Current state](spec/CURRENT-STATE.md). Monthly,
Performance, and Visualisations share one canonical read-only holdings engine;
Monthly now surfaces its quality fields without changing ledger data.

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

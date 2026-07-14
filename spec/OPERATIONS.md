# Operations

This page owns workspace profiles, commands, logs, local servers, Docker, and
release mechanics.

## Profiles and paths

Set the profile before Python imports `ledger.config`:

```powershell
$env:LEDGER_PROFILE = "real"     # default: Statements/ and data/
$env:LEDGER_PROFILE = "example"  # example_data/Statements and example_data/data
```

`LEDGER_DATA_DIR` and `LEDGER_STATEMENTS_DIR` override individual roots.
Derived paths are `ledger.sqlite`, `market.duckdb`, `text_dumps/`, and repo-level
`logs/`. The Click `--profile` option cannot retroactively reload config; the
environment variable is the reliable choice.

## CLI inventory

All Python commands use `uv run`:

```text
ledger db init
ledger pdf dump-all [--institution FOLDER]
ledger pdf dump-samples [--per-folder N]
ledger audit extraction [--statements-dir PATH] [--output PATH]
                        [--institution FOLDER] [--limit N] [--fail-on-errors]
ledger ingest run [--institution FOLDER] [--limit N] [--force]
ledger ingest infer-initials
ledger ingest repair-symbols
ledger ingest reconcile
ledger market refresh [--symbol SYMBOL ...] [--lookback-years N]
ledger market refresh-dividends
ledger market refresh-splits
ledger market refresh-profiles
ledger market refresh-financials
ledger market refresh-earnings
ledger market refresh-fx [--lookback-years N]
ledger market refresh-benchmarks [--symbol SYMBOL ...] [--lookback-years N]
ledger market refresh-all [--lookback-years N]
ledger mcp serve
ledger serve [--host HOST] [--port PORT]
```

`refresh-all` runs held-symbol prices, profiles, dividends, splits, financials,
earnings, and FX. Benchmarks are a separate command. Market fetches primarily
use yfinance; US financial history can fall back to SEC Company Facts.

The extraction audit is read-only with respect to SQLite. It accepts either
source PDFs or stored `.txt` dumps, overwrites a deterministic JSONL report
(default `logs/extraction_audit.jsonl`), and omits raw statement text. Use
`--fail-on-errors` in a gate where invalid/unclaimed/crashed outputs must
return non-zero.

`ledger db init` creates or upgrades schema version 6. For a real existing
ledger, do **not** treat that compatibility migration as the refactor cutover:
first copy the database to a shadow data directory and run the command against
that copy. The API/server does not silently migrate a database at startup; run
`db init` deliberately before serving a v6 database.

## Local development

```powershell
uv sync --all-extras --dev
uv run ledger db init
uv run ledger serve --host 127.0.0.1 --port 8000

cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5175 --strictPort
```

Do not assume ports 5173/5174 belong to this project on a shared machine.
Confirm backend identity through `/openapi.json` (`Trade History API`) and the
frontend title (`Trade History`). Always set Vite's host explicitly to avoid an
IPv6-only listener.

For a Windows frontend process that must survive the launching agent shell,
use PowerShell `Start-Process -WindowStyle Hidden` with stdout/stderr redirected
to repo logs. Prefer Docker Compose for a service that must outlive the coding
session.

## Docker

`docker compose up --build` exposes the backend on 8000 and the built frontend
on 5173. It mounts `data/`, `logs/`, and `Statements/` into the backend. The
profile defaults to `real` and can be overridden via `LEDGER_PROFILE`.

## Logging and privacy

Application logs live under `logs/`; structured market scrape events use
JSONL. At the end of `ledger ingest run`, `ingestion_attempts.jsonl`,
`quarantine.jsonl`, and `skipped_pdfs.log` are regenerated deterministic
indexes of persisted attempts, active quarantine rows, and latest skipped
attempts. They contain source/run/evidence IDs and reasons/statuses, not raw
statement text. Do not commit private statements, text dumps, credentials,
database files, or logs containing statement text. Standard logs and market
scrape events retain their own historical-event semantics.

## Validation

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

Documentation source changes additionally require building and checking
`docs/index.html` with `scripts/build_docs.py`.

## Release

Tags matching `v*.*.*` run `.github/workflows/release.yml`: build docs, commit
the generated HTML to `main` when changed, then build/push the GHCR image. CI
runs Python tests/lint, the frontend build, and the docs freshness check.

Create a tag only after source specs and generated docs agree. The release
workflow is not a substitute for checking docs in a normal pull request.

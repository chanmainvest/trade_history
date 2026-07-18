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
ledger shadow build [--source-db PATH] [--target-db PATH] [--statements-dir PATH]
                    [--report PATH] [--replace] [--no-verify-reproducible]
ledger shadow sign-off --reviewer NAME --confirmation TEXT [--report PATH]
ledger shadow cutover --backend-stopped --confirm-live-db ledger.sqlite
ledger shadow rollback --backup-db PATH --backend-stopped --confirm-live-db ledger.sqlite
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

`ingest reconcile` is CLI-only derived maintenance. It rebuilds conservative
name-only buy/sell links from observed same-currency holdings, transfer pairs,
position attribution, and checkpoint equations. It does not edit statement
PDFs or reported transaction numerics; ambiguous identities remain null.

The extraction audit is read-only with respect to SQLite. It accepts either
source PDFs or stored `.txt` dumps, overwrites a deterministic JSONL report
(default `logs/extraction_audit.jsonl`), and omits raw statement text. Use
`--fail-on-errors` in a gate where invalid/unclaimed/crashed outputs must
return non-zero.

`ledger db init` creates or upgrades schema version 7. The API/server does not
silently migrate a database at startup; run `db init` deliberately before
serving a v6 database. For an existing real ledger, use the guarded shadow
workflow below rather than treating a compatibility migration as live cutover.

## Shadow rebuild, review, and cutover

`ledger shadow build` opens the source SQLite file read-only, copies only
reviewed/user-owned state to a fresh staging database, parses the selected PDF
tree twice, and publishes `data/ledger.vnext.sqlite` only when both clean builds
have the same content fingerprint. It verifies a before/after PDF manifest and
never changes `data/ledger.sqlite`.

```powershell
# Build the default real-profile shadow and its redacted comparison report.
uv run ledger shadow build

# Inspect the local report and perform the required PDF spot checks first.
# Record a human review only after those checks are complete.
uv run ledger shadow sign-off --reviewer "your-name" --confirmation "PDF review complete"

# Stop the backend, then explicitly acknowledge the live filename to switch.
uv run ledger shadow cutover --backend-stopped --confirm-live-db ledger.sqlite
```

The report contains coverage/count/fingerprint comparisons rather than raw
statement values. It includes statement and scope coverage by institution,
period, currency, and a stable redacted account reference; it never writes an
account number into the report. Its reproducibility fingerprint covers active
parser output and semantic ledger state, including scopes, movements, reported
checkpoints, links, inferred/manual initials, and reconciliation equations. It
accounts for account metadata, manual initials, reviewed aliases/lookups,
non-generated reconciliation annotations, and a companion
`ledger.vnext.config.json` copy of portfolio preferences. Source account IDs
are retained in the fresh target so the companion and unchanged live config
remain valid after a database-only cutover. An unmapped reviewed item or
portfolio account ID blocks ordinary sign-off until explicitly acknowledged.
Cutover retains a timestamped backup; `ledger shadow rollback` restores that
backup without deleting it. Shadow build itself never performs cutover.

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

To review a built frontend against a shadow database without changing the live
ledger, bind the database explicitly before any API module is imported:

```powershell
cd frontend; npm run build; cd ..
uv run python scripts/local_review_server.py --database data/ledger.vnext.sqlite --port 5175
```

The review server mounts the API at `/api` and serves SPA fallbacks from
`frontend/dist`. Do not implement a review override by assigning
`sqlite_db.SQLITE_PATH` after importing the API: helper defaults may already be
bound, causing different routes to read different databases and Verify links
to return 404.

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

# AGENTS.md — AI coding-agent instructions

This file is the operating manual for AI coding agents working on
**Trade History** (`trade_history_opus47`). It is intentionally short.

> **Structural detail** — schemas, ingestion design, market-data pipeline,
> per-institution parsing quirks — lives in
> [spec/ARCHITECTURE.md](spec/ARCHITECTURE.md). **Read it before
> changing anything in `src/ledger/db/`, `src/ledger/parsers/`, or
> `src/ledger/market/`.**
>
> Human-facing usage — install, run, tabs walkthrough, file uploads —
> lives in [spec/USER-GUIDE.md](spec/USER-GUIDE.md).
>
> Parser gotchas and symbol-repair lessons live in
> [spec/EXTRACTION-CORNER-CASES.md](spec/EXTRACTION-CORNER-CASES.md).

---

## 0. Cardinal rules

1. This is a **fresh** rebuild. Do **not** copy code from `trade_history/`
   or `trade_history_opus46/`; they are abandoned attempts.
2. Statements PDFs are **read-only inputs**. Never move, rename, or delete
   them.
3. **Quarantine, never fabricate.** If a row can't be confidently parsed,
   it goes to `quarantine_transactions` with `raw_line` + reason. No
   invented numbers, ever.
4. **Native currency only at ingest.** FX conversion is presentation-only.
   See [spec/ARCHITECTURE.md §1.1](spec/ARCHITECTURE.md#11-why-multi-currency-everywhere).
5. **Snapshots are ground truth.** Transactions are the audit trail.
   Holdings on a historical date come from the most recent
   `position_snapshots` for `(account, instrument)` on or before that
   date. See [spec/ARCHITECTURE.md §1.4](spec/ARCHITECTURE.md#14-transactions-snapshots-and-the-reconciliation-gap).
6. **Documentation is part of the code.** Every code change that affects
   behaviour, schema, APIs, CLI commands, data flow, or configuration
   **MUST** be accompanied by matching updates to ALL of the following
   that are relevant — in the same commit, not as a follow-up:

   | File | Update when… |
   |---|---|
   | [spec/ARCHITECTURE.md](spec/ARCHITECTURE.md) | Any change to DB schema, ingestion pipeline, parser protocol, market-data flow, API routes, or workspace-profile logic |
   | [src/ledger/db/schema.sql](src/ledger/db/schema.sql) | Canonical DDL changes (then reflect in spec/ARCHITECTURE.md §2) |
   | [README.md](README.md) | Architecture overview, quick-start steps, tab descriptions, or folder layout changes |
   | [spec/USER-GUIDE.md](spec/USER-GUIDE.md) | Any user-visible behaviour: CLI commands, UI tabs, settings, troubleshooting |
   | [spec/EXTRACTION-CORNER-CASES.md](spec/EXTRACTION-CORNER-CASES.md) | New parser quirks, symbol-repair edge cases, or PDF format discoveries |

   **Enforcement:** Before marking any task complete, re-read every doc
   file listed above and confirm it still accurately describes the code.
   If it does not, update it before finishing. Stale documentation is a
   bug, not a cosmetic issue.

## 1. Tech stack

- **Backend** — Python 3.12+, FastAPI, click CLI.
- **Private DB** — SQLite (`<DATA_DIR>/ledger.sqlite`).
- **Market DB** — DuckDB (`<DATA_DIR>/market.duckdb`).
- **PDF text** — `pdfplumber` (primary) → `pypdf` (fallback). No OCR.
- **Frontend** — React 18 + Vite + TypeScript + Plotly.js + React Query +
  react-router-dom.
- **Tooling** — `uv` for all Python; `npm` for the frontend.

## 2. Tooling rules

- **ALWAYS** use `uv run …` for Python. Never bare `python` or `pip`.
- Frontend changes must pass `npm run build` before commit.
- All scripts log to `logs/<name>.log`. Structured logs use `<name>.jsonl`.
- Use the custom `grep_search`/`file_search`/`read_file` over terminal
  `grep`/`find`/`cat`.

## 2a. Local dev servers — known environment quirks

This workspace runs on a **shared, non-sandboxed machine** alongside other
unrelated projects (e.g. `knowledge_base`). Learned the hard way while
starting the backend/frontend for a user — keep this in mind next time:

- **Don't trust default ports.** `5173`/`5174` are frequently already bound
  by an unrelated project's Vite dev server on this machine. A `200 OK` on
  the expected port is **not** proof it's this app. Verify identity first:
  check the page `<title>` (should be "Trade History") or hit the backend's
  `/openapi.json` and confirm `info.title == "Trade History API"`.
- **Pick an explicit, free port** for the frontend rather than assuming
  5173 is free, e.g. `npm run dev -- --host 127.0.0.1 --port 5175
  --strictPort` (check first with `netstat -ano | Select-String LISTENING`).
- **Vite may bind IPv6-only** (`[::1]:PORT`) if `--host` is omitted, so
  `http://127.0.0.1:PORT` refuses the connection while `http://localhost:PORT`
  silently succeeds via `::1`. Always pass `--host 127.0.0.1` explicitly to
  force IPv4 and avoid a false "it's down" or false "it's up" reading.
- **Node/Vite dev servers die when launched via the agent tool's own
  "detach" flag, even though `uv run uvicorn` (Python) survives fine with
  the same flag.** Root cause: on Windows, a Node.js process terminates by
  default when its console is closed (`CTRL_CLOSE_EVENT`) unless it has its
  *own* console — the agent's detach mechanism doesn't reliably break that
  console association for Node's process tree (`npm.cmd` → `node` →
  `vite.cmd` → `node`), so it dies when the launching shell/session goes
  away, even though it logs "VITE ready" and briefly binds the port first.
  Plain "attached" (non-detached) async processes are worse: they get
  killed at the very next turn/session boundary, confirmed empirically.
  **Fix that works:** spawn it with PowerShell's own `Start-Process
  -WindowStyle Hidden` (redirecting stdout/stderr to files), which gives
  the child its own console and truly detaches it from the tool's
  process tree — verified the resulting process outlives its own spawning
  shell and keeps serving:
  ```powershell
  Start-Process -FilePath node -WorkingDirectory 'frontend' -WindowStyle Hidden `
    -ArgumentList '"node_modules\vite\bin\vite.js" --host 127.0.0.1 --port 5175 --strictPort' `
    -RedirectStandardOutput logs\frontend.log -RedirectStandardError logs\frontend.err.log
  ```
  For anything that must outlive the whole coding session (not just one
  turn), prefer the repo's `docker-compose.yml` over any of the above.

## 3. Workspace profiles

Set **before** Python loads `ledger.config`:

```powershell
$env:LEDGER_PROFILE = "example"   # synthetic data in example_data/
$env:LEDGER_PROFILE = "real"      # default — real Statements/ + data/
```

Override individual paths with `LEDGER_DATA_DIR` and
`LEDGER_STATEMENTS_DIR`. Full table in
[spec/ARCHITECTURE.md §8](spec/ARCHITECTURE.md#8-workspace-profiles).

## 4. Repository layout

```
src/ledger/
  config.py            paths + profile + institution map
  cli.py               `ledger` entry point
  pdf_text.py          PdfText(pages, sha256, …)
  logging_setup.py
  db/                  schema.sql, sqlite.py, duckdb_store.py
  parsers/             one module per institution + types/registry/helpers
  ingest/pipeline.py   walk Statements/, run parser, write SQLite
  market/scrape.py     yfinance → DuckDB
  analytics/           positions/PnL/RRG/correlation/treemap
  api/app.py           FastAPI factory + routes/

frontend/src/          React tabs + i18n + portfolio context + SmartSelect
spec/ARCHITECTURE.md   DB + ingestion + market doc (Mermaid diagrams)
spec/USER-GUIDE.md     human-facing user guide
spec/EXTRACTION-CORNER-CASES.md  parser/symbol repair gotchas
docs/index.html        generated docs site (built by scripts/build_docs.py)
scripts/               one-off CLI helpers + build_docs.py
example_data/          synthetic dataset (LEDGER_PROFILE=example)
tests/                 pytest suite (parsers + analytics)
```

## 5. Required validation after every change

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

For any parser change, also spot-check the output against the cited PDF;
every reported transaction must be defensible against the source.

## 5a. Releasing a new version

On every tagged release the following MUST be done (also automated by
`.github/workflows/release.yml`):

1. **Regenerate `docs/index.html`** from `spec/*.md`:

   ```powershell
   uv run python scripts/build_docs.py --version <tag>
   git add docs/index.html
   git commit -m "docs: regenerate docs/index.html for <tag>"
   ```

2. **Tag and push** — the GitHub Actions workflow then builds and pushes the
   Docker image to `ghcr.io/chanmainvest/trade_history:<tag>` and
   `ghcr.io/chanmainvest/trade_history:latest`.

   ```powershell
   git tag v1.2.3
   git push origin v1.2.3
   ```

## 6. When adding a new parser

See [spec/ARCHITECTURE.md §5](spec/ARCHITECTURE.md#5-adding-a-parser-for-a-new-bank).
Briefly:

1. Add the institution code + folder name to `config.INSTITUTIONS`.
2. Create `src/ledger/parsers/<name>.py` implementing the Parser
   protocol — `NAME`, `VERSION`, `can_handle`, `parse`.
3. Register it in `parsers/registry.py`.
4. Return `list[ParsedStatement]` to handle multi-account / multi-period
   PDFs.
5. Add `tests/test_<name>.py` with at least one fixture covering a
   buy, sell, dividend, option event, and a cash-balance row.
6. Re-ingest with `uv run ledger ingest run` and confirm
   quarantine count doesn't spike unreasonably.

When a new statement type appears that no parser handles, an LLM-assisted
draft parser can be generated from the Settings upload workflow or via
[prompts/new-parser.md](prompts/new-parser.md). Review generated code before
installing it, then add tests and re-ingest.

## 7. Frontend conventions

- Component state via React Query for server data, local `useState` for
  UI only.
- All user preferences flow through `usePortfolio()` (which wraps
  `/config`). Don't store preferences in `localStorage`.
- New user-visible strings should get a key in
  [frontend/src/i18n.tsx](frontend/src/i18n.tsx) and use `t("…")` rather
  than hard-coded English.
- Theme/light-dark uses CSS variables under `:root[data-theme=…]` in
  [frontend/src/styles.css](frontend/src/styles.css). Plotly traces read
  colors from `plotlyTheme()` in
  [frontend/src/theme.ts](frontend/src/theme.ts).

## 8. Deferred items — DONE

All previously deferred items in this section are implemented. Maintain the
same rule going forward: do not silently fabricate parsed data, and document
any future gap or behavioral change in [spec/ARCHITECTURE.md](spec/ARCHITECTURE.md)
and [spec/USER-GUIDE.md](spec/USER-GUIDE.md).

- **DONE — Initial holdings inference** — implemented via
  `uv run ledger ingest infer-initials`. For each (account, instrument)
  it sets `initial_positions.quantity = first_snapshot_qty − Σ pre-snapshot transactions`
  and dates the row one day before the earliest snapshot. For each
  `(account, currency)`, `initial_cash.balance` is inferred from the first
  monthly cash snapshot minus cash transactions up to that date. Idempotent.
  Inferred rows carry `notes LIKE 'inferred:%'` so user-curated rows are
  preserved on re-run; legacy untagged inferred cash rows are replaced with
  tagged rows.
- **DONE — Daily holding reconstruction** — implemented in `/monthly/snapshot`.
  The API uses the latest `position_snapshots.as_of_date` per account as a
  checkpoint, then replays signed transactions after that checkpoint up to
  the requested day. Before the first snapshot, it uses `initial_positions`
  plus transactions. Broker snapshots remain the audit ground truth.
- **DONE — Long-history fundamentals** — implemented for US-listed symbols via
  SEC EDGAR Company Facts fallback in `ledger market scrape`. Non-US
  securities still depend on yfinance unless another free source is added.
- **DONE — PDF upload + new-statement-type extraction via LLM.** API endpoint
  `POST /statements/upload` validates PDF magic bytes, caps uploads at
  25 MiB, sanitizes filenames, saves to `Statements/uploads/`, returns a
  fingerprint, and produces a parse preview. `POST /statements/import`
  imports a reviewed upload with the selected institution folder, then runs
  symbol repair and reconciliation. `POST /statements/draft-parser` writes
  `data/parser_drafts/<sha>/prompt.md` and metadata; when explicitly
  requested, it calls the configured OpenAI / Anthropic / Google provider
  and saves the response for human review before any parser code is installed.
- **DONE — Per-statement extraction explainer UI** — Settings can pick a
  statement and render PDF text lines annotated with parsed transactions,
  position snapshots, and quarantine rows from `GET /statements/explain/{id}`.
- **DONE — Sector data for RRG / treemap / correlation** — implemented via
  `symbol_profiles` in DuckDB and `uv run ledger market refresh-profiles`
  (also included in `refresh-all`). Colors cluster by sector when profile
  metadata is available; unknown symbols fall back to neutral colors.
- **DONE — TD legacy quarterly PDFs (2016-2017)** are split into one statement
  per bundled month/currency, with legacy cash balances and clean holding
  rows parsed where defensible.
- **DONE — RBC annual performance reports** populate `annual_performance_reports`
  with CAD/USD annual money-weighted return summaries. They remain annual
  statements and do not fabricate transactions or position snapshots.
- **DONE — Screenshots** for the README/user guide are captured from the
  synthetic example profile under `docs/screenshots/`.
- **DONE — Transfer-link and reconciliation workflows** populate
  `account_links`, transaction counterpart fields, and
  `position_transaction_links` automatically through
  `ingest.reconcile.reconcile_after_ingest()`. `uv run ledger ingest run`
  calls it after parsing and symbol repair; `uv run ledger ingest reconcile`
  and the Settings reconciliation controls can rebuild links after manual
  edits. Matching remains conservative: ambiguous transfer candidates are
  skipped rather than guessed.

## 9. Ingestion summary (real profile, current state)

After `uv run ledger ingest run` against `Statements/`:

- 324 PDFs scanned, 323 parsed, 1 intentionally skipped
  (`CIBC Tax-Document_58MRB0.pdf`).
- 427 statements, 2,848 transactions, 5,841 position snapshots,
  438 cash balances, 5 annual performance rows.
- 20/20 parser and analytics unit tests pass.
- TD 2025-12 USD statement reconciles to portfolio total within $1.

For full per-institution quirks see
[spec/ARCHITECTURE.md §4.6](spec/ARCHITECTURE.md#46-per-institution-format-notes).

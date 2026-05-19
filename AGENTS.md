# AGENTS.md — AI coding-agent instructions

This file is the operating manual for AI coding agents working on
**ledger** (`trade_history_opus47`). It is intentionally short.

> **Structural detail** — schemas, ingestion design, market-data pipeline,
> per-institution parsing quirks — lives in
> [schema/ARCHITECTURE.md](schema/ARCHITECTURE.md). **Read it before
> changing anything in `src/ledger/db/`, `src/ledger/parsers/`, or
> `src/ledger/market/`.**
>
> Human-facing usage — install, run, tabs walkthrough, file uploads —
> lives in [doc/user-guide.md](doc/user-guide.md).

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
   See [schema/ARCHITECTURE.md §1.1](schema/ARCHITECTURE.md).
5. **Snapshots are ground truth.** Transactions are the audit trail.
   Holdings on a historical date come from the most recent
   `position_snapshots` for `(account, instrument)` on or before that
   date. See [schema/ARCHITECTURE.md §1.4](schema/ARCHITECTURE.md).

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

## 3. Workspace profiles

Set **before** Python loads `ledger.config`:

```powershell
$env:LEDGER_PROFILE = "example"   # synthetic data in example_data/
$env:LEDGER_PROFILE = "real"      # default — real Statements/ + data/
```

Override individual paths with `LEDGER_DATA_DIR` and
`LEDGER_STATEMENTS_DIR`. Full table in
[schema/ARCHITECTURE.md §6](schema/ARCHITECTURE.md#6-workspace-profiles).

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
schema/ARCHITECTURE.md DB + ingestion + market doc
doc/user-guide.md      human-facing user guide
scripts/               one-off CLI helpers (e.g. build_example_data.py)
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

## 6. When adding a new parser

See [schema/ARCHITECTURE.md §3](schema/ARCHITECTURE.md#3-ingestion-pipeline).
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
draft parser can be generated via the prompt skill in
[prompts/new-parser.md](prompts/new-parser.md) (deferred — see §8).

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

## 8. Deferred items (do not silently fabricate; document if you tackle)

These are explicit known gaps. If you implement one, update this list
and the corresponding section in `ARCHITECTURE.md` / `user-guide.md`.

- **Initial holdings inference** — implemented via
  `uv run ledger ingest infer-initials`. For each (account, instrument)
  it sets `initial_positions.quantity = first_snapshot_qty − Σ pre-snapshot transactions`
  and dates the row one day before the earliest snapshot. Same logic for
  `initial_cash`. Idempotent. Inferred rows carry `notes LIKE 'inferred:%'`
  so user-curated rows are preserved on re-run.
- **True transaction-based monthly snapshots.** Current implementation
  forward-fills the snapshot table; brokers occasionally apply lot
  adjustments that only appear on snapshots, so transaction replay is
  not exact. See [schema/ARCHITECTURE.md §1.4](schema/ARCHITECTURE.md).
- **Long-history fundamentals.** yfinance only goes back ~5 years.
  Candidate sources documented in
  [schema/ARCHITECTURE.md §4.4](schema/ARCHITECTURE.md).
- **PDF upload + new-statement-type extraction via LLM.** API endpoint
  `POST /statements/upload` exists as a stub; LLM-driven parser creation
  is not implemented. The Config tab has placeholder slots for
  OpenAI / Anthropic / Google API keys.
- **Per-statement extraction explainer UI** (overlay of PDF → text dump →
  parsed transactions). Backend route `/statements/explain/{id}` is
  stubbed.
- **Sector data for RRG / treemap.** Sector lookups are not in DuckDB;
  RRG uses per-symbol palette colors as a placeholder.
- **TD legacy quarterly PDFs (2016-2017)** emit one statement per file
  (the first month); the bundled later months are not split.
- **RBC annual performance reports** are recorded as empty annual
  statements; the cumulative IRR is not parsed into the schema.
- **Screenshots** for the README/user guide — needs a live browser
  session; deferred until the example_data run-through can be captured.
- **Lint cleanup.** ~45 `E702` (semicolon-joined statements) pre-existing
  warnings in `ruff check src tests`. Functional impact: none. Cosmetic
  cleanup deferred.

## 9. Ingestion summary (real profile, current state)

After `uv run ledger ingest run` against `Statements/`:

- 324 PDFs scanned, 323 parsed, 1 intentionally skipped
  (`CIBC Tax-Document_58MRB0.pdf`).
- 398 statements, 2,776 transactions, 5,519 position snapshots,
  404 cash balances.
- 13/13 parser unit tests pass.
- TD 2025-12 USD statement reconciles to portfolio total within $1.

For full per-institution quirks see
[schema/ARCHITECTURE.md §3.4](schema/ARCHITECTURE.md).

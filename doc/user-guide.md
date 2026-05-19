# Ledger — User Guide

A personal investment ledger for Canadian retail brokers. Imports your
monthly PDF statements from CIBC, HSBC, RBC, and TD, joins them against
free market data, and gives you a single dashboard for your whole
family's accounts.

This guide is written for **humans**. The companion AI-operation rules
live in [AGENTS.md](../AGENTS.md), and the deep technical reference is
[schema/ARCHITECTURE.md](../schema/ARCHITECTURE.md).

---

## 1. Install

```powershell
# clone, then from the repo root:
uv sync
cd frontend ; npm install ; cd ..
```

Requirements:

- Python 3.12 or later.
- Node.js 20 or later.
- Windows or macOS; Linux works but is not the target.

## 2. Start the app

Two terminals.

**Terminal 1 — backend** (default profile uses your real statements):

```powershell
uv run ledger serve
```

The API binds to `http://127.0.0.1:8000`. Auto-reloads on file change.

**Terminal 2 — frontend**:

```powershell
cd frontend
npm run dev
```

Open <http://localhost:5173>. The Vite dev server proxies `/api` to
port 8000.

### 2.1 Try the example dataset first

If you don't have your own statements handy:

```powershell
$env:LEDGER_PROFILE = "example"
uv run python scripts/build_example_data.py
uv run ledger serve
# in another terminal:
$env:LEDGER_PROFILE = "example"   # so the API picks up the example DB
cd frontend ; npm run dev
```

The example dataset has two accounts (TD Direct Investing and Interactive
Brokers) with a current holdings snapshot mirroring the
`portfolio_dashboard/sample_portfolio.xlsx` reference, plus ~37
invented buy/sell/option events spread over 2021-2026.

Reset back to your real data with `$env:LEDGER_PROFILE = "real"` or
just close the terminal.

## 3. Load your statements

1. Drop your PDFs into `Statements/<institution>/` following the
   convention in `config.INSTITUTIONS`. Folders that don't exist are
   skipped, so you can start with just one broker.
2. Ingest:

   ```powershell
   uv run ledger ingest run
   ```

3. Pull market data for everything you hold:

   ```powershell
   uv run ledger market scrape
   ```

   This calls yfinance — be patient and don't run it on a flaky link.
   Re-running is safe; it's an upsert.

4. *(Optional but recommended)* Back-fill positions that pre-date your
   earliest statement:

   ```powershell
   uv run ledger ingest infer-initials
   ```

   This populates `initial_positions` and `initial_cash` so the
   Monthly / Performance views know what you were already holding when
   our records start. Idempotent.

5. Reload the browser. The Transactions tab should now have data.

## 4. The tabs

### 4.1 Transactions

Every event the parser produced, filterable by:

- **Institution** (multi-select with search).
- **Account** (multi-select with search; respects the active portfolio).
- **Symbol** (multi-select with search; scrolls inside max 2/3 screen
  width).
- **Type** (buy / sell / dividend / option_… etc., multi-select).
- **Min |amount|** — 100 / 1k / 10k / 100k / 1M presets.
- **Date range**.

Click a symbol cell to jump to the Research tab.

### 4.2 Monthly snapshot

Default view: holdings as of the most recent statement date in the
database, across all accounts in the active portfolio.

- **As of** date — pick any day; the app uses the most recent snapshot
  on or before it.
- **Compare to** — picking a second date adds a `Δ` column. Rows where
  the position grew are tinted green, rows that shrank are tinted red,
  in git-diff style. Positions that disappeared between the two dates
  appear at the bottom.
- Columns sortable; institution / account chips are clickable filters.

### 4.3 Performance

Total portfolio market value over time.

- **Period** buttons: 1m / 3m / 6m / 1y / 3y / 5y / 10y / max / custom.
- **Currency**: CAD / USD / Both.
- **Show as %** — rebases all series to 100 at the start of the window;
  forced on if "Hide $ values" is set in Settings.
- **Portfolio / institution / account** filters (multi-select).
- Cash chart toggles off automatically when "Hide $ values" is on.

The chart no longer zig-zags between accounts whose statement dates
don't line up — see
[schema/ARCHITECTURE.md §1.4](../schema/ARCHITECTURE.md) for the
forward-fill rationale.

### 4.4 Research

Per-symbol deep dive.

- Type a ticker, press **Enter** (no Go button — it was useless).
- **Period** buttons + daily/weekly/monthly resample.
- Candlestick with **MA50 / MA200** toggles, volume sub-chart.
- Your buy/sell marks overlay the chart. **Solid triangles** =
  equity / ETF trades, **hollow triangles** = option trades, so you
  can tell them apart at a glance.
- **Financials** chart underneath — quarterly or annual, per-metric
  show/hide. (Note: yfinance only gives ~5 years; see deferred items
  in AGENTS.md for plans to extend.)
- **Trade history** table at the bottom lists every transaction the
  app has for this symbol, including account / description.

### 4.5 Visualisations

Three views, all filtered by the active portfolio:

- **RRG (Relative Rotation Graph)** — animated, with adjustable trail.
  Each symbol gets a stable color. Use the checkbox row below the chart
  to hide individual symbols.
- **Treemap** — holdings grouped by asset type, sized by market value.
  Defaults to the latest snapshot date (so it's never blank unless you
  actually have no holdings).
- **Correlation matrix** — square heatmap using the same color scale as
  `portfolio_dashboard`. **Sort-by** dropdown reorders both axes by
  correlation against the chosen symbol.

### 4.6 Settings

- **Theme** — light / dark.
- **Language** — English / 繁體 (HK) / 繁體 (TW) / 简体 (CN). Flag picker
  in the top right.
- **Display currency** — CAD or USD.
- **Hide $ values** — show percentages only (useful for screenshots).
- **Portfolios** — add named groups of accounts. Example:
  - *Dad's TFSA* → CIBC TFSA only.
  - *Kids' RESPs* → TD WB only.
  - *Household* → all accounts (the default `all` portfolio).

  The dropdown in the top right activates a portfolio across **every**
  tab.

## 5. PDF upload (beta)

A `POST /statements/upload` endpoint exists for in-browser PDF upload.
Today it accepts the file, fingerprints it, and runs the existing
parsers if the institution can be recognized. If no parser claims the
file, the response is `unrecognized` and (when an LLM API key is
configured) the app will prompt to draft a new extraction routine.

LLM-driven parser drafting is **not implemented yet**; the API-key
slots in Settings are placeholders. See `AGENTS.md` deferred items.

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Vite crashes with `Failed to resolve` on Windows | Vite realpath'd the workspace onto another drive | Already fixed via `resolve.preserveSymlinks: true` in `frontend/vite.config.ts` |
| Treemap is blank | Picked a date with no snapshot | Pick a date ≥ your earliest statement; the API falls back to the latest available |
| Performance chart goes flat to zero | One account's data gap | Forward-fill is on by default; if you really want raw, pass `forward_fill=false` to `/api/performance/total` |
| `BOUGHT` appears as a symbol in RBC rows | Pre-fix bug | Re-ingest after pulling. The parser now strips leading verbs and applies a small name-to-ticker map (e.g. iShares 20+ → TLT) |
| Empty option symbol on a CIBC `option_expiration` row | Pre-fix bug | Re-ingest. The parser now recognizes `CALL ROOT MON DD YYYY STRIKE` shapes |

## 7. Data privacy

- The SQLite DB and the source PDFs **never leave your machine**.
- The DuckDB market DB contains only public market data and is safe to
  share or commit.
- The browser app talks only to `127.0.0.1` by default.
- No telemetry. No analytics. No third-party fonts loaded from the CDN.

## 8. Where to learn more

- [schema/ARCHITECTURE.md](../schema/ARCHITECTURE.md) — schemas,
  ingestion design, market-data pipeline, parser quirks.
- [AGENTS.md](../AGENTS.md) — rules for AI agents editing this repo.
- [example_data/README.md](../example_data/README.md) — what's in the
  synthetic dataset and how to rebuild it.

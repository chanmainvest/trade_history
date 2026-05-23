# Trade History — User Guide

A personal investment tracker for Canadian retail brokers. Imports your
monthly PDF statements from CIBC, HSBC, RBC, and TD, joins them against
free market data, and gives you a single dashboard for your whole
family's accounts.

This guide is written for **humans**. The companion AI-operation rules
live in [AGENTS.md](../AGENTS.md), and the deep technical reference is
[ARCHITECTURE.md](ARCHITECTURE.md).

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

### 2.1 Docker start

If you have Docker Desktop installed, one command starts both containers:

```powershell
docker compose up --build
```

Open <http://localhost:5173>. The frontend is a production Vite build served by
nginx; `/api/*` is proxied to the FastAPI backend container. Your local
`data/`, `logs/`, and `Statements/` folders are mounted into the backend, so the
same database and PDFs are used.

### 2.2 Try the example dataset first

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
invented buy/sell/option events spread over 2021-2026. Option transaction
quantities are contracts, not underlying shares. The builder also seeds an
example DuckDB with market prices, FX rates, and sector/profile metadata for
the sample symbols so Research, Performance, Correlation, and Treemap work
without using your real database.

Example-data screenshots:

![Transactions tab with example data](../docs/screenshots/transactions-example.png)

![Monthly snapshot tab with example data](../docs/screenshots/monthly-example.png)

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

  Re-running is fast when PDFs are unchanged because the ingester compares
  `source_files.sha256` before parsing. Use `uv run ledger ingest run --force`
  after parser upgrades when you intentionally want to re-parse unchanged
  PDFs.

3. Pull market data for everything you hold:

    ```powershell
    uv run ledger market refresh
    ```

    This calls yfinance and, for US-listed fundamentals, the free SEC
    EDGAR Company Facts API. Be patient and don't run it on a flaky link.
    Re-running is safe; it's an upsert.

    To fetch sector/profile metadata used by Visualization colors, run:

    ```powershell
    uv run ledger market refresh-profiles
    ```

    `uv run ledger market refresh-all` includes profiles, prices,
    dividends, splits, financials, earnings, and FX in one pass.

4. *(Optional but recommended)* Back-fill positions that pre-date your
    earliest statement:

    ```powershell
    uv run ledger ingest infer-initials
    ```

    This populates `initial_positions` and `initial_cash` so the
    Monthly / Performance views know what you were already holding when
    our records start. Idempotent. Inferred rows are tagged with
    `notes = 'inferred:...'`; any reviewed/manual rows with a different
    note prefix are preserved on re-run. Cash rows are inferred from the
    first monthly cash snapshot for each account/currency.

5. *(Optional after parser upgrades)* Repair already-ingested legacy
    symbols without deleting PDFs or re-ingesting everything. The repair
    uses known ticker mappings first, then matches transactions to holdings
    from the same statement where the PDF only printed a security name:

    ```powershell
    uv run ledger ingest repair-symbols
    ```

6. Reload the browser. The Transactions tab should now have data.

## 4. The tabs

### 4.1 Transactions

Every event the parser produced, filterable by:

- **Institution** (multi-select with search).
- **Account** (multi-select with search; respects the active portfolio).
- **Symbol** (multi-select with search; scrolls inside max 2/3 screen
  height).
- **Type** (buy / sell / dividend / option_… etc., multi-select).
- **Min |amount|** — 100 / 1k / 10k / 100k / 1M presets.
- **Date range**.

Click a symbol cell to jump to the Research tab.

### 4.2 Monthly snapshot

Default view: holdings as of the most recent statement date in the
database, across all accounts in the active portfolio.

- **As of** date — pick any day; the app starts from the latest account
  holdings snapshot checkpoint and replays transactions after it. Empty
  statement rows without holdings are ignored as checkpoints. Before the
  first snapshot, it uses inferred/manual initial holdings.
- **Compare to** — picking a second date adds a `Δ` column. Rows where
  the position grew are tinted green, rows that shrank are tinted red,
  in git-diff style. Positions that disappeared between the two dates
  appear at the bottom.
- **Sync compare to as of** — copies the current **As of** date into the
  comparison date when you want to reset the diff.
- Cash positions appear as `CAD Cash` / `USD Cash` rows per account. Totals
  show native currency buckets plus combined CAD and USD totals using the
  latest FX rate on or before the snapshot date.
- Columns sortable; institution / account / profile are visible, and the
  filters can narrow the table to specific institutions or accounts.

### 4.3 Performance

Total portfolio value over time, including cash.

- **Period** buttons: 1m / 3m / 6m / 1y / 3y / 5y / 10y / max / custom.
- **Currency**: CAD / USD / Both.
- **Show as %** — rebases all series to 100 at the start of the window;
  forced on if "Hide $ values" is set in Settings.
- **Portfolio / institution / account** filters (multi-select).
- The separate cash chart remains available as a cash-only breakdown and
  toggles off automatically when "Hide $ values" is on.

The chart no longer zig-zags between accounts whose statement dates
don't line up, and sold-out holdings are cleared when later broker
snapshots omit them — see
[ARCHITECTURE.md §1.4](ARCHITECTURE.md#14-transactions-snapshots-and-the-reconciliation-gap) for the
forward-fill rationale.

### 4.4 Research

Per-symbol deep dive.

- Type a ticker, press **Enter** (no Go button — it was useless). When the
  search box is empty it lists known tickers; typing narrows the list by
  ticker, asset type, or currency. The list scrolls at a max 2/3 viewport
  height so it stays usable on smaller screens.
- **Period** buttons + daily/weekly/monthly resample.
- Candlestick with **MA50 / MA200** toggles, volume sub-chart.
- Your buy/sell marks overlay the chart. **Solid triangles** =
  equity / ETF trades, **hollow triangles** = option trades, so you
  can tell them apart at a glance.
- **Financials** chart underneath — quarterly or annual, per-metric
  show/hide. yfinance data is extended with SEC EDGAR Company Facts for
  US-listed symbols when available.
- **Trade history** table at the bottom lists every transaction the
  app has for this symbol, including account / description.

### 4.5 Visualisations

Three views, all filtered by the active portfolio, with institution and
account filters inside the tab:

- **RRG (Relative Rotation Graph)** — animated, with adjustable trail.
  Symbols in the same sector use related colors when profile metadata is
  available. Use the checkbox row below the chart to hide individual
  symbols.
- **Treemap** — holdings sized by market value. Use **Group by** to switch
  between institution/account, type, and sector. Use **Performance** to pick
  the period used for green/red coloring on the holding tiles. Defaults to
  the latest snapshot date (so it's never blank unless you actually have no
  holdings).
- **Correlation matrix** — square heatmap using the same color scale as
  `portfolio_dashboard`. **Sort-by** dropdown, top/left ticker labels, or
  cells can reorder both axes by correlation against a symbol; checkboxes
  hide/show individual symbols and use sector-colored accents.

### 4.6 Settings

- **Theme** — light / dark.
- **Language** — English / 繁體 (HK) / 繁體 (TW) / 简体 (CN). Flag picker
  in the top right.
- **Hide $ values** — show percentages only (useful for screenshots).
- **Portfolios** — add named groups of accounts. Example:
  - *Dad's TFSA* → CIBC TFSA only.
  - *Kids' RESPs* → TD WB only.
  - *Household* → all accounts (the default `all` portfolio).

  The dropdown in the top right activates a portfolio across **every**
  tab.

## 5. PDF upload (beta)

A `POST /statements/upload` endpoint exists for in-browser PDF upload.
Today it accepts only files named `.pdf` whose bytes start with PDF magic
bytes, rejects empty files and files over 25 MiB, sanitizes the upload
filename, saves the file under `Statements/uploads/`, and returns its
SHA-256 fingerprint. It does not yet run a review/import workflow.

LLM-driven parser drafting is **not implemented yet**; the API-key
slots in Settings are placeholders. See `AGENTS.md` deferred items.

## 6. MCP server for AI agents

Run the Ledger MCP server over stdio:

```powershell
uv run ledger mcp serve
```

Use that command in any MCP-capable AI client. A typical server entry looks like
this, with the working directory set to the repo root:

```json
{
  "servers": {
    "ledger": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "ledger", "mcp", "serve"],
      "env": { "LEDGER_PROFILE": "real" }
    }
  }
}
```

The server provides tools for frontend routes/config, allowlisted API GET
requests, and bounded CLI actions such as ingest, symbol repair,
initial-position inference, and market-data refresh. It intentionally does not
offer arbitrary shell access.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Vite crashes with `Failed to resolve` on Windows | Vite realpath'd the workspace onto another drive | Already fixed via `resolve.preserveSymlinks: true` in `frontend/vite.config.ts` |
| Treemap is blank | Picked a date with no snapshot | Pick a date ≥ your earliest statement; the API falls back to the latest available |
| Performance chart drops to zero | All filtered holdings were sold or omitted by the latest broker checkpoint | Broaden the account/symbol filters, or pass `forward_fill=false` to `/api/performance/total` if you want raw checkpoint sums |
| `BOUGHT` appears as a symbol in RBC rows | Pre-fix bug | Run `uv run ledger ingest repair-symbols` or re-ingest after pulling. The parser now strips leading verbs and applies a small name-to-ticker map (e.g. iShares 20+ → TLT) |
| Empty option symbol on a CIBC `option_expiration` row | Pre-fix bug | Run `uv run ledger ingest repair-symbols` or re-ingest. The parser now recognizes `CALL ROOT MON DD YYYY STRIKE` shapes |

## 8. Data privacy

- The SQLite DB and the source PDFs **never leave your machine**.
- The DuckDB market DB contains only public market data and is safe to
  share or commit.
- The browser app talks only to `127.0.0.1` by default.
- No telemetry. No analytics. No third-party fonts loaded from the CDN.

## 9. Where to learn more

- [ARCHITECTURE.md](ARCHITECTURE.md) — schemas,
  ingestion design, market-data pipeline, parser quirks.
- [AGENTS.md](../AGENTS.md) — rules for AI agents editing this repo.
- [example_data/README.md](../example_data/README.md) — what's in the
  synthetic dataset and how to rebuild it.

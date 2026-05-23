# Trade History — Personal Multi-Broker Trading History & Analytics

A from-scratch pipeline + web app to consolidate every brokerage statement I
have (CIBC Imperial Service, CIBC Investor's Edge, CIBC TFSA, HSBC Direct
Invest, RBC Direct Investing, TD WebBroker) into one coherent database, then
visualize positions, P&L, performance and per-ticker research.

> Status: under active construction. See `AGENTS.md` for the in-progress
> implementation plan and conventions.

## High-level architecture

```mermaid
flowchart TD
    PDF["Statements/&lt;Institution&gt;/*.pdf"] --> PDFT["pdf_text.py\npdfplumber → pypdf fallback\nimage-only PDFs skipped"]
    PDFT --> PARSE["parsers/&lt;institution&gt;.py\nper-broker parser\nemits ParsedStatement"]
    PARSE --> INGEST["ingest/pipeline.py\nnormalises + writes to SQLite"]
    INGEST --> SQLITE[("data/ledger.sqlite\ntransactions · positions\nstatements · cash")]
    FEED["yfinance\n+ SEC EDGAR"] --> SCRAPE["market/scrape.py\nrate-limited HTTP"]
    SCRAPE --> DUCKDB[("data/market.duckdb\ndaily_prices · dividends\nsplits · financials\nearnings · fx_rates")]
    SQLITE --> API["FastAPI backend"]
    DUCKDB --> API
    API --> UI["React + Vite + TS\n① Transactions\n② Monthly snapshot\n③ Performance\n④ Stock research\n⑤ Visualisations"]
```

## Database split

Two stores, deliberately separated:

- **SQLite — `data/ledger.sqlite`**: my private trading data. Multi-currency
   by design. Tables: `institutions`, `accounts`, `account_links`,
   `instruments`, `instrument_aliases`, `source_files`, `statements`,
   `transactions`, `quarantine_transactions`, `position_snapshots`,
   `cash_balances`, `initial_positions`, `initial_cash`,
   `annual_performance_reports`, `position_transaction_links`. Schema is in
   [src/ledger/db/schema.sql](src/ledger/db/schema.sql).
   Ingest automatically links unambiguous account transfers and rebuilds
   position-to-transaction attribution after parsing.
- **DuckDB — `data/market.duckdb`**: public market data scraped on demand.
  Tables: `daily_prices`, `dividends`, `splits`, `option_implied_vol`,
  `fx_rates`, `financials_quarterly`, `financials_annual`,
   `earnings_events`, `symbol_profiles`, `scrape_log`. Defined in
  [src/ledger/db/duckdb_store.py](src/ledger/db/duckdb_store.py).

Cash balances are tracked per `(account, currency)` so CAD and USD never get
mixed at ingest. FX conversion is a presentation-layer concern.

## Quick start

### Local development

```powershell
# 1. install
uv sync

# 2. initialise both DBs
uv run ledger db init

# 3. dump first-page text of every statement (used to design parsers)
uv run ledger pdf dump-samples

# 4. ingest all statements
uv run ledger ingest run

# 5. infer opening holdings before the first statement
uv run ledger ingest infer-initials

# 6. optional after manual DB edits: rebuild transfer/reconciliation links
uv run ledger ingest reconcile

# 7. scrape market data for every held ticker
uv run ledger market refresh

# 8. start backend
uv run uvicorn ledger.api.app:app --reload

# 9. start frontend
cd frontend && npm install && npm run dev
```

All scripts log to `logs/<command>.log` (and JSONL where structured).

## Screenshots

These screenshots use the synthetic example profile, not real account data.

![Transactions tab with example data](docs/screenshots/transactions-example.png)

![Monthly snapshot tab with example data](docs/screenshots/monthly-example.png)

### Docker

```powershell
docker compose up --build
```

Open <http://localhost:5173>. The frontend container serves the built Vite app
with nginx and proxies `/api/*` to the backend container. The backend also
binds to <http://localhost:8000> for direct API checks. Local `data/`, `logs/`,
and `Statements/` folders are mounted into the backend container.

### MCP server

Trade History includes a stdio Model Context Protocol server for AI agents:

```powershell
uv run ledger mcp serve
```

The MCP tools expose allowlisted frontend/API operations and bounded backend
CLI operations such as ingest, symbol repair, initial-position inference, and
reconciliation rebuilds. They do not provide arbitrary shell access.

## Folder layout

```
trade_history_opus47/
├── AGENTS.md            ← agent rules + architecture deep-dive
├── README.md
├── pyproject.toml
├── data/                ← SQLite + DuckDB live here (git-ignored)
├── logs/                ← every script writes here (git-ignored)
├── docs/                ← generated docs + synthetic screenshots
├── Statements/          ← raw PDFs per institution (git-ignored)
├── scripts/             ← one-shot CLIs (refresh, ingest, scrape)
├── src/ledger/
│   ├── config.py
│   ├── logging_setup.py
│   ├── pdf_text.py
│   ├── cli.py
│   ├── db/              ← schema + sqlite/duckdb helpers
│   ├── parsers/         ← one sub-package per institution
│   ├── ingest/          ← orchestrates parser → DB
│   ├── market/          ← yfinance + scraping for DuckDB
│   ├── analytics/       ← P&L, asset value, sector rotation, correlation
│   └── api/             ← FastAPI app + routes
├── frontend/            ← React + Vite + TS, Plotly
└── tests/
```

## Frontend tabs

1. **Transactions** — every txn across every account; filterable by date,
   account, institution, ticker and type.
2. **Monthly** — pick any date and view consolidated holdings reconstructed
   from the latest account snapshot plus subsequent transactions; diff two
   dates side-by-side. Cash positions are shown per account/currency, with
   native CAD/USD totals and combined CAD/USD totals from FX rates.
3. **Performance** — total portfolio value over time, including cash;
   filter by account / institution / ticker / asset class.
4. **Stock research** — clicking any symbol anywhere drills in here; an empty
   search box lists known tickers and narrows as you type. The view includes a
   candlestick price chart with 50/200 MA toggles + my buy/sell markers,
   volume sub-chart, then quarterly financial-statement chart with
   per-line visibility toggles. Daily/weekly/monthly switch.
5. **Visualisations** — sector rotation (RRG) with date scrubber + play
   button, treemap grouped by institution/account, type, or sector with
   green/red period performance coloring, and a sortable correlation matrix
   with clickable ticker headers.
6. **Settings** — portfolios/theme/language, statement upload review/import,
   local parser-draft prompt bundles with optional LLM provider response,
   statement extraction explainer, and reconciliation rebuild/summary.

## Data quality rule

Correctness > completeness. Any ambiguous statement line goes to
`quarantine_transactions` with `raw_line` + reason, never silently dropped
and never partially fabricated.

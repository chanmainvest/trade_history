# Architecture

This document is the authoritative description of how data flows through
the **ledger** app. [AGENTS.md](../AGENTS.md) intentionally stays short
(AI-ops rules only) and points here for any structural detail.

The app has three core data planes:

1. **Private SQLite** — your statements, transactions, holdings, cash.
2. **Public DuckDB** — market data (prices, dividends, splits, financials, FX).
3. **JSON config** — user preferences (portfolios, theme, language, LLM keys).

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  PDF statements  │ → │  parsers + ingest │ → │  SQLite (private)│
└──────────────────┘   └──────────────────┘   └────────┬─────────┘
                                                       │
┌──────────────────┐   ┌──────────────────┐   ┌────────▼─────────┐
│ yfinance / stooq │ → │  market.scrape   │ → │  DuckDB (public) │
└──────────────────┘   └──────────────────┘   └────────┬─────────┘
                                                       │
                                          ┌────────────▼──────────┐
                                          │  FastAPI  →  React UI │
                                          └───────────────────────┘
```

---

## 1. SQLite schema (private)

The canonical DDL lives in [`src/ledger/db/schema.sql`](../src/ledger/db/schema.sql).
This section explains *why* it's shaped the way it is.

### 1.1 Why multi-currency everywhere?

Every monetary column is paired with a `currency` column. Cash is tracked
per `(account, currency)`. Reasons:

- A Canadian discount-brokerage account can hold both CAD and USD cash legs
  simultaneously. The statement reports each leg in its native currency.
- FX rates change daily. If we stored a CAD-equivalent at ingest time it
  would be frozen at the wrong rate by the time the user views it.
- Performance reports must be reproducible. Converting on the fly from
  `fx_rates` (DuckDB) gives the same number on any future view.

**Rule:** ingest stores native amounts. FX is presentation-only.

### 1.2 Why are options first-class instruments?

`instruments.asset_type` enumerates
`equity / etf / option / mutual_fund / bond / cash / other`.
Option rows carry `option_root`, `option_expiry`, `option_strike`,
`option_type`, `option_multiplier`.

The **UNIQUE** key includes the option fields, so:

- `AAPL` (the equity) and `PUT AAPL JAN 15 2027 200` (one of many options)
  are *separate* rows with a stable shared `option_root = AAPL`.
- The same underlying with different strikes/expiries does not collide.
- The Research tab can pull either by symbol+root.

The 100x multiplier is stored on each option row so future contracts with
non-standard multipliers (split-adjusted) can be represented faithfully.

### 1.3 Why split `source_files` from `statements`?

`source_files` is **one row per PDF on disk** — fingerprinted by
`sha256`. `statements` is **one row per (account, period)** *snapshot*
emitted by parsing that PDF.

One PDF can legitimately produce multiple `statements` rows because:

- **RBC** statements pack CAD-currency and USD-currency sections into a
  single PDF; each is its own statement.
- **TD WebBroker** prints `<acct>-CDN` and `<acct>-USD` sub-statements in
  one PDF.
- **HSBC** fee-summary PDFs cover multiple periods at once.

Splitting the model lets re-ingest stay idempotent (the file is unchanged,
the derived snapshots are recomputed) and lets reports group by
`(account_id, period_end)` without re-parsing.

### 1.4 Transactions, snapshots, and the reconciliation gap

Two parallel tables converge on the same ground truth from different
angles:

- `transactions` — the *events* (buy, sell, dividend, option roll, …).
- `position_snapshots` — the *state at the end of each statement*.

Holdings on any historical date come from the most recent
`position_snapshots` row for `(account, instrument)` on or before that
date. Forward-filling between snapshots is done by the Performance route
to avoid zig-zag (per-account statements arrive on staggered dates).

**Why we don't replay transactions to compute holdings:** brokers
sometimes apply lot adjustments, splits, name changes, and book-cost
roll-ups that *only* appear on the snapshot side. Treating the snapshot
as ground truth makes the app robust to those adjustments. Transactions
are the audit trail.

`initial_positions` and `initial_cash` are for the period *before* the
first available statement — they let you record an opening balance for
holdings that pre-date your earliest PDF.

### 1.5 In-kind transfers between MY accounts

When a holding moves from CIBC Investor's Edge to a CIBC TFSA, both
statements show one half of the move. The schema captures both:

- `account_links` — date-level link (transfer_date, from, to, notes).
- `transactions.counterpart_account_id` / `counterpart_txn_id` —
  event-level pairing.

This lets the Performance and Monthly tabs trace a lot across accounts
without double-counting cash.

### 1.6 The quarantine principle

Any unparseable line goes to `quarantine_transactions` with
`raw_line + reason`. **We never fabricate a transaction.** A confidence
score below 1.0 is allowed in `transactions.parser_confidence`, but
every recorded row must be defensible against the source PDF.

The mirror file [`logs/quarantine.jsonl`](../logs/quarantine.jsonl) is
the same content for grep-friendly investigation.

---

## 2. DuckDB schema (public)

DDL: [`src/ledger/db/duckdb_store.py`](../src/ledger/db/duckdb_store.py).

```
daily_prices         (symbol, trade_date)    OHLCV + adjusted close
dividends            (symbol, ex_date)
splits               (symbol, split_date)    ratio
option_implied_vol   (symbol, trade_date)    ATM IV surface
fx_rates             (pair, trade_date)      e.g. USDCAD
financials_quarterly (symbol, period_end)    revenue, NI, FCF, EPS…
financials_annual    (symbol, period_end)
earnings_events      (symbol, event_date)
scrape_log           (symbol, kind, fetched_at, status, message)
```

Every table has a natural-key `PRIMARY KEY` so a re-scrape upserts and
never doubles rows.

### 2.1 Why DuckDB instead of more SQLite?

- Market data is bulk-append columnar data. DuckDB's columnar
  storage + Parquet-style compression handles 15+ years of daily prices
  for hundreds of symbols in tens of MB.
- DuckDB ships full SQL window functions and resampling, which the
  Research tab uses for weekly/monthly OHLC roll-ups.
- It runs in-process so the FastAPI server doesn't need a separate
  database server.

### 2.2 Why is it separate from the private DB?

The DuckDB file is **safe to publish or share** — it has no personal
information. Splitting it makes it trivial to: redact a snapshot for
support, ship the example dataset with empty market data, or scrape
opportunistically without contaminating the private store.

---

## 3. Ingestion pipeline

```
Statements/<institution>/*.pdf
        │
        │   ledger.pdf_text.extract_pdf
        ▼                                            (pdfplumber → pypdf fallback)
   PdfText(pages, sha256, page_count, …)
        │
        │   parsers.registry.select_parser
        ▼                                            (folder name → first-page sniff)
   Parser.parse(pdf) → ParseResult
        │
        │   ingest.pipeline.ingest_one
        ▼
   SQLite upserts: source_files / statements / transactions
                   instruments / position_snapshots / cash_balances
                   quarantine_transactions
```

Entry point: `ledger ingest run` (see [`src/ledger/ingest/pipeline.py`](../src/ledger/ingest/pipeline.py)).

### 3.1 The Parser protocol

```python
class Parser(Protocol):
    NAME: str
    VERSION: str
    def can_handle(self, folder_name: str, first_page_text: str) -> bool: ...
    def parse(self, pdf: PdfText) -> ParseResult: ...
```

Contracts:

- **Deterministic.** Two runs on the same PDF produce byte-identical
  output (so re-ingest doesn't churn rows).
- **Side-effect free.** Parsers emit dataclasses from
  [`parsers/types.py`](../src/ledger/parsers/types.py); the ingest
  pipeline owns *all* DB writes.
- **Multi-statement aware.** Every parser returns
  `list[ParsedStatement]` inside `ParseResult` to handle multi-account
  and multi-period PDFs.

### 3.2 Parser selection

`select_parser(folder_name, first_page_text)`:

1. If `folder_name` matches a known institution folder, return that
   institution's parser. Folder mapping is in
   [`config.INSTITUTIONS`](../src/ledger/config.py).
2. Otherwise, sniff the first page text for a parser-specific
   signature (e.g. "RBC Direct Investing", "TD WebBroker").
3. Fall back to `generic.GenericParser` (currently a no-op stub).

### 3.3 The vocabulary of `txn_type`

Every transaction emitted by every parser MUST use one of the literals
defined in `parsers/types.TxnType`:

```
buy / sell / short_sell / buy_to_cover
option_buy_to_open / option_sell_to_open
option_buy_to_close / option_sell_to_close
option_assignment / option_exercise / option_expiration
dividend / distribution / interest_income
interest_expense / margin_interest
transfer_in / transfer_out / journal
deposit / withdrawal
tax_withholding
fee / commission / adjustment / fx_conversion
stock_split / name_change / spinoff / merger / return_of_capital
```

Adding a new type requires updating both the literal *and* the analytics
that switch on it (Monthly, Performance, Research).

### 3.4 Per-institution format notes

| Institution | Folder | Quirks |
|---|---|---|
| CIBC Imperial Service | `CIBC Imperial Service/` | Monthly. `ð` PDF artifact replaced with em-dash. `(continued)` headers ignored when splitting sections. |
| CIBC Investor's Edge | `CIBC Invest Direct/` | Monthly. `Tax-Document_*.pdf` skipped. Option expiry `MM/DD/YY`. |
| CIBC TFSA | `CIBC TSFA/` | Same engine as CIBC IE; account-type heuristic detects TFSA. |
| HSBC Direct Invest | `HSBC direct invest/` | pdfplumber drops spaces; `_normalize()` re-inserts space after `MmmDD`. Compact options `PUT-100TLT'2616JA@75`. Multi-account per PDF. |
| RBC Direct Investing | `RBC Invest Direct/` | One PDF holds CAD + USD statements → 2 `ParsedStatement` rows. Full month names. Trailing-hyphen negatives. |
| TD WebBroker | `TD Webbroker/` | Two sub-accounts per PDF (`<acct>-CDN` / `<acct>-USD`). Option positions span two lines. Legacy 2016-2017 quarterly format. |

### 3.5 Symbol resolution (and synthetic symbols)

Statements frequently identify a security by a free-form name with no
parens-ticker. The app's strategy, in order:

1. **Parens ticker** — if `(AAPL.NASDAQ)` appears, use it.
2. **Inline option spec** — if `CALL SOXS JAN 16 2026 55.00` appears,
   build an option instrument directly.
3. **Known-name map** — see
   [`parsers/name_resolver.py`](../src/ledger/parsers/name_resolver.py).
   E.g. `ISHARES 20 PLUS YEAR TREASURY → TLT`.
4. **Synthetic symbol** — strip leading verbs (`BOUGHT`/`SOLD`/...) and
   join the first 4 words with underscores. These synthetic symbols are
   filtered out of `market.scrape._held_symbols` by a strict regex so
   yfinance is never asked for `BOUGHT_ISHARES_20_PLUS`.

This keeps the audit trail honest: the description always matches the
PDF, even if the canonical ticker can't be inferred.

### 3.6 Logs

- `logs/ingest.log` — main run.
- `logs/parser_<institution>.log` — per-parser detail.
- `logs/skipped_pdfs.log` — image-only PDFs we skip (no OCR).
- `logs/quarantine.jsonl` — same content as `quarantine_transactions`.

---

## 4. Market data pipeline

```
held symbols (from SQLite)  ──┐
manual extras (config)     ──┼─→  market.scrape  ─→  DuckDB
benchmark symbols          ──┘
```

Entry point: `ledger market scrape` (see
[`src/ledger/market/scrape.py`](../src/ledger/market/scrape.py)).

### 4.1 What gets scraped

1. **Symbol universe** = `_held_symbols(sqlite)` ∪ benchmarks (`SPY`,
   `XIU.TO`, `VTI`, configurable) ∪ `--symbol` CLI overrides.
2. Synthetic symbols are filtered out by a strict regex
   (`^[A-Z][A-Z0-9-]{0,8}(\.[A-Z]{1,3})?$`) plus a blocklist
   (`BOUGHT`, `CASH`, `CAD`, …).
3. For each surviving symbol:
   * Daily OHLCV from yfinance (default lookback 15 years).
   * Dividends and splits (yfinance accessors).
   * Quarterly and annual financials (yfinance).
   * Earnings events (yfinance).
4. FX pairs: USD/CAD, EUR/USD, etc. (configurable).

### 4.2 Rate limiting and retries

yfinance is unofficial and rate-limited. The scraper:

- Sleeps `--per-folder` seconds between symbols (default 2s).
- Retries on `HTTPError 429` with exponential backoff, max 3 attempts.
- Writes every attempt to `logs/market_scrape.jsonl` (one JSON line per
  request) so partial runs can be resumed.

### 4.3 Idempotency

Every DuckDB table has a natural-key `PRIMARY KEY`. The scraper uses
`INSERT OR REPLACE`, so re-running on an existing DB is safe and only
updates the most recent rows. `scrape_log` records `(symbol, kind,
fetched_at, status)` so the CLI can show "what's stale" without
hitting yfinance.

### 4.4 Why yfinance and not a paid feed?

- Free. The app is personal.
- Covers >99% of symbols a Canadian retail investor would hold.
- Returns split- and dividend-adjusted close, which is the right
  signal for the Performance tab.

Drawbacks:

- Unofficial — API can break with no notice.
- No fundamentals beyond ~5 years of history.
- No option chains. (Deferred.)

Candidates for a backup feed if yfinance breaks:

- [stooq.com](https://stooq.com) (free CSV downloads, 25+ years).
- [SEC EDGAR companyfacts API](https://www.sec.gov/edgar/sec-api-documentation)
  (fundamentals, longer history, US only).
- [Alpha Vantage](https://www.alphavantage.co) (free tier, daily limit).

These would slot in as alternative implementations behind
`market.scrape._fetch_daily(symbol, start, end)`.

---

## 5. Config / portfolios

Stored as `<DATA_DIR>/config.json`. Schema:

```jsonc
{
  "portfolios": [
    { "id": "all",     "name": "All accounts",   "account_ids": [] },
    { "id": "dad",     "name": "Dad's TFSA",     "account_ids": [3, 7] },
    { "id": "kids",    "name": "Kids' RESPs",    "account_ids": [9] }
  ],
  "active_portfolio": "all",
  "theme": "dark",
  "display_currency": "CAD",
  "hide_money": false,
  "language": "en"
}
```

The frontend reads it via `GET /config` and writes via `PUT /config`.
A portfolio with empty `account_ids` is implicitly "all accounts".

---

## 6. Workspace profiles

Configured by env var, read on module load by
[`config.py`](../src/ledger/config.py):

| `LEDGER_PROFILE` | `STATEMENTS_DIR`     | `DATA_DIR`            | Purpose |
|---|---|---|---|
| `real` (default) | `Statements/`        | `data/`               | Your real statements |
| `example`        | `example_data/Statements/` | `example_data/data/`  | Synthetic demo data |

Override individual paths with `LEDGER_DATA_DIR` /
`LEDGER_STATEMENTS_DIR` for ad-hoc runs.

The example dataset is rebuilt by:

```powershell
$env:LEDGER_PROFILE = "example"
uv run python scripts/build_example_data.py
```

It loads two synthetic accounts (TD Direct Investing + Interactive
Brokers) directly into SQLite — no PDFs needed — with the current
holdings mirroring `portfolio_dashboard/sample_portfolio.xlsx` and
~37 invented buy/sell/option events stretching back to 2021.

---

## 7. Required validation after structural changes

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

Spot-check parser output against the corresponding PDF; every reported
transaction must be defensible against the source.

# Analysis & Visualization improvements — review, plan, and results

Date: 2026-08-15. Pre-approved by the user; results to be reviewed in the
morning. Covers the three analysis surfaces — **Viz** (RRG / treemap /
correlation), **Research** (per-symbol chart + financials + trades), and
**Performance** (portfolio value over time) — plus the market-data pipeline
that feeds them.

## Part 1 — Review of the current implementation

### What already works well

- Clean separation: all three pages read server state through React Query;
  no local-storage preferences; theme via `plotlyTheme()`.
- Research is genuinely useful for an investor who trades around a core
  portfolio: candlesticks + MA50/200 + your own buys/sells (stock solid
  triangles, options hollow) + volume, ticker-change lineage stitched via
  `ticker_segments`, and a full trade table per symbol.
- Viz treemap groups by account / asset type / sector with performance
  coloring; correlation matrix is sortable by any column with per-symbol
  show/hide; RRG animates with trails and quadrant shading.
- Data layer is sound: DuckDB holds 15y of daily prices for ~90 symbols
  including benchmarks, plus dividends, splits, financials, earnings, FX.

### Problems found

**Data freshness / coverage (biggest usefulness killer)**

1. `data/market.duckdb` was last refreshed 2026-04-30 — every chart, RRG
   frame, and performance color was 3.5 months stale, and nothing in the UI
   told the user.
2. `symbol_profiles` was **empty** (0 rows), so treemap "Group by: Sector",
   sector legend colors, correlation sector borders, and RRG sector colors
   all silently degraded to "Unknown" grey.
3. Benchmarks (SPY etc.) were present only because of an old manual run;
   `refresh-all` does not include them, so RRG would break again over time.

**Correctness / honesty**

4. The treemap sums `market_value` across **native currencies** (CAD + USD
   mixed into one rectangle tree) with no FX conversion and no warning —
   against the project rule that FX conversion is presentation-only and
   must be explicit.
5. Treemap leaf colors encode performance but there is no legend, so a new
   user cannot tell green = gain, red = loss, grey = no data.

**Investor value gaps**

6. Research has no summary: no last close, period return, 52-week range,
   or position context; the user must eyeball the chart.
7. Performance shows raw market value per currency only: no combined
   portfolio value (needs FX, which `fx_rates` already has), and no
   benchmark comparison to answer "am I beating the market?".
8. Dividends and splits are downloaded into DuckDB but never shown
   anywhere.

**Convention violations**

9. Viz.tsx and Research.tsx hardcode English UI strings ("Holdings
   treemap", "As of:", "Benchmark:", "Play/Pause", "Financials",
   "Trade history for …", …) instead of `t(...)` from `i18n.tsx`, breaking
   the repo rule and the zh-HK/zh-TW/zh-CN translations.

## Part 2 — Plan (implemented tonight)

| # | Change | Where | Why |
|---|---|---|---|
| 1 | Run `market refresh-all` + `refresh-benchmarks` | CLI | Fresh data; populate `symbol_profiles` so sector views light up |
| 2 | Freshness badge: "price data through {date}" + stale warning | Viz, Research (computed from rows; no new endpoint) | Trust — user sees how current charts are |
| 3 | i18n all hardcoded strings (en/zh-HK/zh-TW/zh-CN) | `i18n.tsx`, Viz, Research | Convention + translations |
| 4 | Research summary strip: last close, period %, 52w high/low, YTD | Research (frontend-only from fetched rows) | Investor context at a glance |
| 5 | Treemap: color legend (gain/loss/nodata) + native-currency note with per-currency totals | Viz | Honesty about what colors and sums mean |
| 6 | Performance: combined CAD/USD total converted at the as-of FX rate (presentation-only), shown alongside per-currency series | viz/monthly FX helper reuse | One number for "how big is my portfolio" |
| 7 | Performance: benchmark overlay (SPY default) rebased to the selected period, toggleable | Performance via existing `/research/prices` | "Am I beating the market?" |
| 8 | Update `spec/API-UI.md` for new visible behavior; run docs build | spec | Docs are part of the change |

Deliberately **not** done tonight (larger follow-ups, listed for review):

- Money-weighted (XIRR) / net-of-deposits returns on Performance — needs a
  deposits/withdrawals extraction across all institutions; worth doing but
  too easy to get silently wrong at 1am. Suggested next.
- Dividend/split event markers on the Research chart (data is already in
  DuckDB; needs one small endpoint).
- Drawdown / volatility panels; currency-hedged totals; per-account RRG.

## Part 3 — Results (filled in after implementation)

- Market download: see "Download outcome" below.
- Code: commits listed at the bottom of this file (also in git log).
- Validation: pytest 139 passed, ruff clean, `npm run build` clean,
  `build_docs.py --check` current.

### Download outcome

- `ledger market refresh-all` + `refresh-benchmarks` ran twice (once after
  expanding symbol coverage). Prices now run through **2026-08-14**
  (previously 2026-04-30) for **114 symbols**; FX through 2026-08-15
  (USD/CAD 1.3872).
- Root cause of thin coverage found and worked: the scraper only downloads
  symbols with a *verified* Yahoo mapping in `instrument_market_symbols`
  (21 before). Ran `ledger ingest resolve-instruments --verify-yahoo`,
  which verified 12 more (33 total; 14 candidates resolved, 194 refused as
  ambiguous, 927 not found — the conservative resolver will not guess).
- Remaining gap for morning review: 141 traded equities/ETFs vs 33 verified
  mappings. Widening this needs human review of the ambiguous candidates
  (by design — a shared company name is not evidence). Suggested follow-up:
  a small review workflow over the 194 ambiguous candidates.

### Verification

- `pytest -q`: 139 passed; `ruff check src tests`: clean;
  `npm run build`: clean; `build_docs.py --check`: current.
- API smoke test against real data: `/performance/total` returns 3,902
  USD/CAD points; `/viz/holdings_by_sector` returns 98 holding rows with
  `price_data_through=2026-08-14` and 74 with period performance;
  `/viz/rrg` returns 3,649 animation frames for SPY.

### Commits

- `api: add fx series and price freshness to analysis feeds`
- `ui: investor improvements to viz, research, and performance`
- `docs: rebuild index and record analysis plan`

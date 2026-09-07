# API and UI

The backend is FastAPI (`ledger.api.app:app`) and the frontend is React/Vite.
Ledger data routes are query-only. `PUT /config` is the one current HTTP write;
it updates preferences in JSON, not SQLite.

## Current routes

| Prefix | Routes | Consumer/purpose |
|---|---|---|
| root | `GET /health` | liveness and app identity |
| `/transactions` | list (including read-only opening positions; filterable by date, institution, account, symbol, type, currency, min \|amount\|), accounts, referenced symbols, transaction types, currencies, latest date. `symbols`/`txn-types`/`currencies` accept an optional `account_id` CSV to scope the option lists | Transactions/filter controls |
| `/monthly` | `GET /dates`, `GET /snapshot`, `GET /diff` | canonical point-in-time holdings and comparison |
| `/export` | `GET /yahoo-csv` | selected portfolio as a Yahoo Finance lots-import CSV |
| `/performance` | `GET /total`, `GET /cash` | canonical holdings value series and reported cash checkpoints |
| `/research` | `GET /prices`, `/trades`, `/financials` | dated multi-ticker security research |
| `/viz` | `GET /holdings_by_sector`, `/correlation`, `/rrg` | visual analytics |
| `/config` | `GET`, `PUT` | portfolios/theme/hide-money/language |
| `/statements` | list, `GET /{id}/pdf`, `GET /{id}/boxes` | read-only extraction/reconciliation verification |

There are no upload, import, LLM draft-parser, explainer, or HTTP
reconciliation-rebuild endpoints in the current route set.

## Tabs

1. Transactions: filterable ledger events and explicit opening positions.
2. Monthly: snapshot/date comparison and native/converted totals; the active
   portfolio comes from the top-bar selector. Snapshot dates are chosen from
   `GET /monthly/dates` (dates that hold a complete checkpoint) via a
   month-stepper picker — prev/next arrows plus a dropdown grouped by year —
   never a free-form calendar. A Compare toggle reveals a second picker with
   swap and quick ranges (1M/3M/6M/YTD/1Y snapped to the nearest available
   date); the resolved checkpoint date is displayed when it differs from the
   selection. An "Export Yahoo CSV" action downloads the viewed snapshot's
   holdings from `GET /export/yahoo-csv` in Yahoo Finance's lots-import
   format (`Symbol, Trade Date, Purchase Price, Quantity`, empty Trade Date):
   only instruments with a Yahoo `candidate`/`verified` market-symbol mapping
   are exported, short (non-positive) quantities are exported with quantity
   ``0`` (Yahoo rejects negative lots), and cash, incomplete holdings, and
   unmapped instruments are skipped — the skipped symbols are reported below
   the toolbar.
3. Performance: native-currency value/cash history with bounded forward fill.
4. Research: price, trade, and fundamental detail; moving averages use full
   fetched history before the visible period is clipped. Dated ticker lineages
   are stitched without using post-change prices under the old symbol.
5. Visualisations: holdings treemap, correlation, and RRG.
6. Verify extraction: PDF.js rendering with persisted evidence rectangles,
   parsed transaction/position/cash/summary lists first, one concise status,
   and detailed scope issues/reconciliation/quarantine/diagnostics below.
7. Settings: named account portfolios.

Global top-bar controls select portfolio, language, hidden-money mode, and
theme. Preferences flow through `usePortfolio()` and React Query.

## Verify contract and limitations

`GET /statements` includes read-only quality counters/flags for unresolved
identity or quarantine rows, incomplete scopes/reconciliation input, and
unexplained residuals. The React filter checkboxes apply those flags locally to
the fetched picker rows.

`GET /statements/{id}/boxes` returns the active parser/run metadata, every
currency/section/scope completeness declaration, persisted position/cash/total
reconciliation result, structured scope blockers, and the parsed lists. It
returns only the physical page numbers explicitly owned by the logical
statement and never copies sibling-statement quarantine rows. It reads exact evidence-to-line
links produced by the separate layout-enrichment command; it does not fuzzy
match text during an HTTP request. Each row reports its geometry status/method.
Repeated text without a unique semantic page/line hint is visibly `ambiguous`;
unmatched, coordinate-free, and legacy rows remain visibly unlinked rather
than receiving a plausible wrong box. Legacy single-box evidence remains a
persisted compatibility fallback. A legacy database without v6
scope/reconciliation tables returns explicitly empty quality facts rather than
treating those facts as complete.
`/verify?statement=<id>&ref=<kind>:<id>` is authoritative initial state and
cannot be replaced by the newest-statement default. The UI waits for the target
physical page/overlay before using pane-local scroll math. A right-row click
scrolls only the PDF pane; a PDF-box click leaves that pane fixed and reveals
the right row. Only boxes containing that exact reference receive selected
styling.

Financial rows are grouped by native currency, then transactions, positions,
cash, and statement totals. Cash renders opening and closing as separate
rows: persisted evidence keeps the opening line under the `cash` reference
kind and maps later lines to `cash_close`, so each balance row links to its
own box; legacy single-box cash evidence has no separate closing line and its
closing row falls back to the shared box. Total
rows show printed opening, change, and closing values when available; missing
printed values remain blank. Transaction and position rows carry the
instrument's asset type and, for option contracts, the option type, strike,
expiry, and multiplier;
Verify renders option contracts with that contract detail while equity and
ETF rows keep the plain symbol form. This keeps dual-currency RBC review within one
currency block.

## Configuration shape

```json
{
  "portfolios": [{"id": "all", "name": "All accounts", "account_ids": []}],
  "active_portfolio": "all",
  "theme": "dark",
  "hide_money": false,
  "show_source_links": true,
  "language": "en"
}
```

An empty `account_ids` list means all accounts. Legacy `display_currency` and
`llm_keys` keys are removed on read/write.

## Holdings API contract

Monthly, Performance, and Visualisation routes share the read-only
`ledger.holdings.holdings_at()` service. It anchors only on complete scoped
checkpoints, applies normalized position/cash movements afterward, and never
writes SQLite. Monthly rows include a stable `holding_key` made from account,
canonical instrument key, and currency; diff rows use the same identity.
For a ticker lineage the stable key uses its root identity, while the row also
returns `ticker_symbols` and displays the symbol valid at the requested date.

Research responses include `requested_symbol`, current `symbol`, ordered
`symbols`, and dated `ticker_changes`. Prices are selected only inside each
symbol's validity window, trades include every linked instrument, and
financial periods prefer the newest applicable ticker. The UI displays the
ticker history and current symbol.

Each holdings row also returns checkpoint statement/scope identifiers, an
optional exact `source_ref`, and a `provenance` object that distinguishes one
reported row from a checkpoint plus contributing movements,
the broker-facing `symbol` and distinct `market_symbol`,
reported-versus-reconstructed state, reconciliation status/reason, price/date
status, and quality warnings. A composite formed from two or more non-zero
complete position scopes has nullable singular `scope_key`, source, checkpoint,
and reconciliation fields and `provenance.type = multiple_checkpoints`.
`provenance.checkpoints` is an ordered list of contributor bundles containing
one scope key, checkpoint date, statement ID, snapshot-set ID, contributed
quantity, position source ref, and movement refs from the same contributor.
The aggregate is incomplete, reconstructed, and not reported. Broker cost,
profit/loss, and valuation from an individual contributor are null; only an
independent native-currency listing quote may provide aggregate market price
and value with `price_status = market`.

Monthly preserves the existing single-source icon for ordinary rows. For a
`multiple_checkpoints` row it instead shows a compact multiple-source marker,
one Verify link for each linkable contributor, and a non-clickable marker for
each contributor without defensible geometry; it never promotes one
contributor to an authoritative aggregate source. The checkpoint and quality
cells explicitly identify multiple complete checkpoints using translated text.
Monthly otherwise renders checkpoint date, holding state, reconciliation
state, and compact incomplete/reconciliation/pricing warnings.
Native-currency totals remain primary; CAD/USD conversions display their rate
and rate date. When both currencies are present but no FX rate exists for the
as-of date, the UI shows per-currency totals only and a hint that the combined
total is unavailable.

`GET /performance/total` also returns `usd_cad` (date, rate) pairs from the
market database so Performance can draw a presentation-only combined
CAD-equivalent series; the rate and rate date are shown next to it. `GET
/viz/holdings_by_sector` returns `price_data_through` (latest `daily_prices`
trade date) and accepts a `currency` display-currency parameter (`CAD` or
`USD`, default `CAD`): every row's `market_value` is converted
presentation-only from its native currency via the latest `fx_rates` rate on
or before the as-of date, the response reports the `fx` rate/date used, and
`unconverted_currencies` lists any currencies left native when no rate
exists. The Treemap renders a CAD/USD toggle (default CAD) and shows the
conversion note. Research computes its summary strip (last close, period change,
52-week range) client-side from the fetched price rows. All three analysis
tabs show the price-data date and warn when it is a week or more stale.

`GET /viz/assets` returns one row per held asset (holdings aggregated across
the scoped accounts) for the Visualisations **Assets** table: resolved
holdings checkpoint date (`as_of_date`), latest close/adj close on or before
the selected date, Yahoo `market_cap`, 30-day annualized realized (historical)
volatility computed from `daily_prices`, the latest `option_implied_vol` IV
at or before the selected date with its trade date, and per-window sparkline
series (`1w`/`1mo`/`3mo`/`1y`/`3y`, downsampled to ≤24 adjusted closes) with
each window's first→last % change. Holdings resolve to the latest complete
checkpoint on or before the selected date while the market side clamps to the
selected date itself, so the default (today) view shows the freshest prices.
When a Yahoo close is unavailable, `price` falls back to the broker-reported
unit value when one is present and `price_source` is `broker`; otherwise the
price is null. The frontend prefixes broker fallbacks with `~`, hides the
Value column, and makes every displayed header sortable. The table respects
the tab's institution/account filters and its sparkline cells shade red/green
by the window's loss/gain magnitude with the signed percentage overlaid.

## Frontend rules

- Use React Query for server data and component state only for UI concerns.
- Add translated strings in `frontend/src/i18n.tsx`.
- Use CSS variables for theme colors and `plotlyTheme()` for charts.
- Keep account filtering consistent with the active portfolio. Transactions
  and Visualisations expose an explicit **All accounts** control when a
  portfolio is active so users can bypass the portfolio filter without
  selecting individual accounts. Transactions, Verify, and Visualisations
  narrow their Institution/Account filter options to
  the active portfolio's accounts (the **All accounts** control or the
  portfolio picker's `all` entry restores the full list); Transactions also
  scopes its Symbol/Type/Currency option lists and prunes any selected filter
  values the active portfolio excludes, and Visualisations prunes its
  Institution/Account selections the same way.
- Transactions moves the Institution/Account/Type/Symbol/Currency filters into
  Excel-style funnel icons on the column headers and sorts client-side: a
  header click toggles ascending/descending (numeric for quantity, price, and
  amount; empty values last). Date-range and min-|amount| stay in the toolbar.
- Verify renders PDF pages lazily as they enter the viewport; evidence overlays
  size to the PDF.js viewport so boxes stay aligned with the rendered canvas.
- Source icons in Transactions and Monthly obey `show_source_links`; they deep
  link only when the server reports exact/unique geometry and are absent when
  no defensible source reference exists. IDs alone never promise linkability.
  Composite holdings use a multiple-source marker, preserve non-linkable
  contributors as non-clickable evidence, and never display one contributor
  as the aggregate's singular source.
- The treemap sizes rectangles in each holding's native currency, states the
  per-currency totals, and never silently converts; the gain/loss/no-data
  color legend is rendered above the chart.
- Performance can overlay a benchmark ETF (rebased % from the selected
  period start) alongside the native-currency and combined CAD-equivalent
  series; selecting a benchmark forces the % view.
- A frontend change must pass `npm run build`.

# API and UI

The backend is FastAPI (`ledger.api.app:app`) and the frontend is React/Vite.
Ledger data routes are query-only. `PUT /config` is the one current HTTP write;
it updates preferences in JSON, not SQLite.

## Current routes

| Prefix | Routes | Consumer/purpose |
|---|---|---|
| root | `GET /health` | liveness and app identity |
| `/transactions` | list (including read-only opening positions), accounts, referenced symbols, transaction types, latest date | Transactions/filter controls |
| `/monthly` | `GET /snapshot`, `GET /diff` | canonical point-in-time holdings and comparison |
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
   portfolio comes from the top-bar selector.
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
cash, and statement totals. Cash shows opening and closing separately. Total
rows show printed opening, change, and closing values when available; missing
printed values remain blank. This keeps dual-currency RBC review within one
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
status, and quality warnings. Monthly renders checkpoint date, holding state,
reconciliation state, and compact incomplete/reconciliation/pricing warnings.
Native-currency totals remain primary; CAD/USD conversions display their rate
and rate date.

## Frontend rules

- Use React Query for server data and component state only for UI concerns.
- Add translated strings in `frontend/src/i18n.tsx`.
- Use CSS variables for theme colors and `plotlyTheme()` for charts.
- Keep account filtering consistent with the active portfolio.
- Source icons in Transactions and Monthly obey `show_source_links`; they deep
  link only when the server reports exact/unique geometry and are absent when
  no defensible source reference exists. IDs alone never promise linkability.
- A frontend change must pass `npm run build`.

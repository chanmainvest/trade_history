# API and UI

The backend is FastAPI (`ledger.api.app:app`) and the frontend is React/Vite.
Ledger data routes are query-only. `PUT /config` is the one current HTTP write;
it updates preferences in JSON, not SQLite.

## Current routes

| Prefix | Routes | Consumer/purpose |
|---|---|---|
| root | `GET /health` | liveness and app identity |
| `/transactions` | list, accounts, symbols, transaction types, latest date | Transactions/filter controls |
| `/monthly` | `GET /snapshot`, `GET /diff` | canonical point-in-time holdings and comparison |
| `/performance` | `GET /total`, `GET /cash` | canonical holdings value series and reported cash checkpoints |
| `/research` | `GET /prices`, `/trades`, `/financials` | security research |
| `/viz` | `GET /holdings_by_sector`, `/correlation`, `/rrg` | visual analytics |
| `/config` | `GET`, `PUT` | portfolios/theme/hide-money/language |
| `/statements` | list, `GET /{id}/pdf`, `GET /{id}/boxes` | read-only extraction/reconciliation verification |

There are no upload, import, LLM draft-parser, explainer, or HTTP
reconciliation-rebuild endpoints in the current route set.

## Tabs

1. Transactions: filterable ledger events.
2. Monthly: snapshot/date comparison and native/converted totals.
3. Performance: value/cash history.
4. Research: price, trade, and fundamental detail.
5. Visualisations: holdings treemap, correlation, and RRG.
6. Verify extraction: PDF.js rendering with `pdfplumber` text-line boxes,
   parsed transaction/position/cash/summary/quarantine lists, scope
   completeness, active parser/run metadata, and reconciliation outcomes.
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
reconciliation results, and the parsed lists. It re-extracts PDF text lines,
normalizes whitespace/case, and fuzzy-matches stored transaction, position,
cash, summary-total, and quarantine evidence text. Repeated text can match
multiple boxes; legacy rows with no recorded source text remain visibly
unlinked. A legacy database without v6 scope/reconciliation tables returns
explicitly empty quality facts rather than treating those facts as complete.

## Configuration shape

```json
{
  "portfolios": [{"id": "all", "name": "All accounts", "account_ids": []}],
  "active_portfolio": "all",
  "theme": "dark",
  "hide_money": false,
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

Each holdings row also returns checkpoint statement/scope identifiers,
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
- A frontend change must pass `npm run build`.

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
| `/statements` | list, `GET /{id}/pdf`, `GET /{id}/boxes` | read-only extraction verification |

There are no upload, import, LLM draft-parser, explainer, or HTTP
reconciliation-rebuild endpoints in the current route set.

## Tabs

1. Transactions: filterable ledger events.
2. Monthly: snapshot/date comparison and native/converted totals.
3. Performance: value/cash history.
4. Research: price, trade, and fundamental detail.
5. Visualisations: holdings treemap, correlation, and RRG.
6. Verify extraction: PDF.js rendering with `pdfplumber` text-line boxes and
   parsed transaction/position/cash/quarantine lists.
7. Settings: named account portfolios.

Global top-bar controls select portfolio, language, hidden-money mode, and
theme. Preferences flow through `usePortfolio()` and React Query.

## Verify limitations

`GET /statements/{id}/boxes` re-extracts text lines, normalizes whitespace/case,
and fuzzy-matches stored transaction/position/quarantine `raw_line` values.
Cash rows do not have raw lines, so they cannot be source-linked. Repeated text
can match multiple boxes. Coordinates are not stored as ingestion provenance.

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
status, and quality warnings. The current React UI uses stable identity keys but
does not yet render the quality fields; Phase 8 owns that read-only surface.

## Frontend rules

- Use React Query for server data and component state only for UI concerns.
- Add translated strings in `frontend/src/i18n.tsx`.
- Use CSS variables for theme colors and `plotlyTheme()` for charts.
- Keep account filtering consistent with the active portfolio.
- A frontend change must pass `npm run build`.

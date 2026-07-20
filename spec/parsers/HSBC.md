# HSBC parser

Implementation: `src/ledger/parsers/hsbc.py`, parser name `hsbc`, current
version `2.5.0`.

## Recognition and account shape

HSBC PDFs can contain several account sections. The parser emits one statement
per printed account and infers currency from the account type or the observed
`-E` (CAD) / `-F` (USD) suffix convention. Annual fee summaries emit annual
records with no fabricated monthly holdings.

Adjacent sections for the same account are merged before scope declaration, so
a continued account page does not create a duplicate account-period statement.

## State and evidence handling

- Normalization repairs compact dates such as `Sep5 Bought` without corrupting
  valid closing-balance text such as `Sep30 Closing Balance`.
- The state machine tracks account, currency, holdings/activity section, and
  continuation rows. It preserves compact option contracts and printed/
  parenthesized holding symbols.
- Repeated holdings column headers such as `Description Quantity ... (CAD)` are
  skipped before row parsing, so `CAD`/`USD` header labels cannot become
  position tickers.
- Parentheses and trailing-negative money text flow through the shared money
  parser. A cash scope is complete only with a valid printed closing balance;
  invalid quantities, cash values, or unclaimed numeric rows are quarantined
  rather than made zero.
- Holding names containing digits are sliced from the final financial columns,
  not split at the first digit. Distinct printed lots for one contract/listing
  are aggregated at persistence; exact duplicate raw rows remain deduplicated.
- `Convert$`/`Convert` FX legs and printed refunds participate in the cash
  equation. Repeated legal/footer text is filtered before it can become a
  holding.
- Parsed transactions, positions, cash, and quarantine rows receive source
  spans. Real PDF coordinates are retained when available; text-only fallback
  evidence is deterministic page/line information.
- Account sections retain their physical pages while they are merged, and
  incomplete scopes emit structured blockers rather than only a generic status.
- Normalized name/symbol/ticker-change activity verbs are recognized; the
  shared contract still requires both printed symbols before linking them.
- Activity descriptions without an independently printed ticker are explicitly
  marked unresolved. Compact strings such as `BCEINC`, `NUTRIENLTD`, or an ETF
  name can be resolved by the shared listing catalog/known-instrument pipeline,
  but can no longer pass through as a ticker by regex shape alone.

## Remaining limits

- Some old holdings lack an explicit symbol. The parser preserves the printed
  identity or quarantines uncertainty; resolution remains a separate reviewed
  ingest step.
- New account-header variants and broker column changes require a fixture or
  PDF spot-check before declaring their sections complete.
- Complete scopes are parser evidence, not proof that a cash or position
  equation balances. They feed the reconciliation engine and still require
  source review when it reports a residual.

Fixtures cover two accounts/currencies, parentheses negatives, compact options,
continued account pages, cash, and source evidence. See
[PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for the shared output rules.

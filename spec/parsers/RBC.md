# RBC parser

Implementation: `src/ledger/parsers/rbc.py`, parser name `rbc`, current
version `2.7.0`.

## Recognition and account shape

The parser handles monthly Direct Investing statements and annual investment
performance reports. Monthly text is split at `CDN $` / `U.S. $` blocks; annual
reports emit annual performance summaries and do not create monthly movements
or positions.

For a monthly PDF, all CAD and USD blocks for the same physical account and
period are aggregated into **one** `ParsedStatement`. They are represented as
separate native-currency positions/cash snapshot scopes, so a source cannot
overwrite the first currency while writing the second.

## State and evidence handling

- Asset Review and Account Activity are separate state-machine sections.
  Continued page markers are ignored and continuation text remains attached to
  the open activity row rather than becoming a new verb. Historical compact
  extraction such as `AUG.10`, `OpeningBalance(...)`, and
  `ClosingBalance(...)` is accepted. Activity ends at the printed closing
  balance so dated rows under Open Orders are not recorded as executions.
- Printed call/put, root, expiry, strike, multiplier, and quantity form the
  option identity. Unknown numeric holding/activity rows are quarantined.
- Debit/credit direction uses page-word geometry from RBC's printed columns.
  When one row contains both withholding debit and gross-income credit, its
  cash effect is the net credit minus debit. Printed signs remain fallback
  evidence, including a trailing-negative option quantity such as `20-`.
  Invalid quantity or closing-cash text is quarantined; no numeric parse
  failure becomes zero.
- An in-kind transfer whose number is in the quantity column has zero cash
  effect plus a signed security quantity/instrument. A nominal-cost buy with
  only quantity plus one other number uses geometry to distinguish a rate with
  zero cash from a cash-column amount.
- Compact `TFR OUT`/`TRFIN<reference>` account-transfer variants retain their
  printed direction and cash column. If the Activity cell is blank but a dated
  row has an unambiguous debit/credit value, the parser stores a generic cash
  adjustment without inventing an income or instrument subtype.
- Name-only fallback identities are explicitly unresolved. They may match one
  exact same-statement holding during staged resolution but cannot persist as
  invented ticker symbols.
- When the historical curated name fallback is applicable, it receives the
  row's native currency so it cannot collapse CAD/USD dual listings.
- A recognized Asset Review currency block declares a complete positions scope;
  a cash scope is complete only with a valid printed closing balance.
- Parsed transactions, positions, cash, and quarantines receive source spans,
  including coordinate/word evidence when extraction exposes it.
- Currency blocks retain their physical page membership, and every incomplete
  scope carries a structured evidence-linked blocker.
- Explicit name/symbol/ticker-change activity is retained, but a relationship
  is emitted only when both old and new symbols are printed.
- A dividend followed by a printed fund-series code and `REINVEST @` becomes
  `reinvest_dividend`: printed units and price are retained against the matching
  mutual-fund holding, with zero cash effect.
- Strict printed `RBF###`/`RBF####` codes remain broker identifiers, not
  inferred market-provider tickers.

## Remaining limits

- RBC has historical column variants. The parser derives each page's cash
  boundaries from its own `RATE`/`DEBIT`/`CREDIT` header. A new layout needs a
  fixture or source spot-check before its sign mapping is trusted.
- `TRANSFER TO/FROM` and ambiguous journals remain event-specific and must not
  be paired or balanced by parser invention.
- Complete parser scopes feed the residual engine. An approved re-ingest/shadow
  rebuild is still required before they improve the dated live ledger.

Fixtures cover one dual-currency monthly account, compact historical activity,
multi-page activity, option transactions, cash, and annual-performance parsing. See
[PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for the shared output rules.

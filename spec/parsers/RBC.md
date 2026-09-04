# RBC parser

Implementation: `src/ledger/parsers/rbc.py`, parser name `rbc`, current
version `2.8.2`.

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
  `ClosingBalance(...)` is accepted. Activity dates print either month form —
  abbreviated (`AUG. 10`) or full (`JULY 31`) — and both parse; a dated row
  whose date falls outside the statement period is quarantined, never
  recorded. Activity ends at the printed closing
  balance so dated rows under Open Orders are not recorded as executions.
  The per-page furniture ("Cdn./U.S. Dollar Statement <year>",
  "Your Account Number: … n of m", and the repeated
  "Order Execution Only <MMM. DD>" page header) is skipped in both sections
  rather than quarantined.
- Corporate-action legs print in-kind quantities with no cash: `MGR`/
  `MERGER` rows become merger legs and `EXCHANGE` rows become in-kind
  journals. When the wrapped description carries the printed broker fund
  code — `(648)`, the same number Asset Review prints as `RBF648` — the leg
  resolves to that fund symbol (`printed_fund_code`); the dividend path
  falls back to the same code when the fund is no longer held. A currency
  block with an Asset Review but no printed Account Activity (a month with
  no trades in that currency) still declares its positions scope.
- Printed call/put, root, expiry, strike, multiplier, and quantity form the
  option identity. 2021-2022 statements lose spaces in text extraction: the
  verb prints squeezed to its root (`CALLSHOP`, `CALL.NTR`) and the FX-rate
  and section-total furniture prints without any spaces
  (`(Exchangerate1USD=...)`, `TotalValueofOther`); the optional verb space
  and squeezed-furniture match keep both parseable. Unknown numeric
  holding/activity rows are quarantined.
  The Asset Review "Other" section is not options-only: rows that do not
  match the option grammar fall through to the standard holding-row grammar
  (for example BHP depositary shares printed next to a PUT contract), and
  only rows matching neither grammar quarantine.
  Footnote markers printed inside a holding row — `#` marks a book cost
  obtained from a source other than RBC — are furniture: the marker is
  dropped before matching, the amounts still parse, and the quarantined or
  stored `raw_line` keeps the printed row verbatim.
- A holding row may wrap: RBC prints the share-class / security-type text
  under the holding line and restates the quantity — `COM NEW 1,500` or a
  bare `2,000` under a Common Shares row, `AMERICAN DEPOSITARY SHARES ON
  1,700` plus `ECH RPSNTNG TWO ORD SHS` for a two-line depositary wrap, or
  the option underlying's issuer name under an option row. The wrapped text
  attaches to the previous holding row as its printed security description
  (stored as `position_snapshots.security_description`); the restated
  quantity is duplicate evidence retained in `raw_line`, never a second
  position. Page-break furniture between wrapped lines — the
  `-CONTINUEDONNEXTPAGE-` marker and the footnotes block — is skipped so it
  cannot attach to the open holding. A restated number that disagrees with
  the holding row quarantines as `continuation quantity does not match the
  holding row`.
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

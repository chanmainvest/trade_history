# RBC parser

Implementation: `src/ledger/parsers/rbc.py`, parser name `rbc`, current
version `2.3.0`.

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
  the open activity row rather than becoming a new verb.
- Printed call/put, root, expiry, strike, multiplier, and quantity form the
  option identity. Unknown numeric holding/activity rows are quarantined.
- Debit/credit direction uses RBC event semantics plus printed signs, including
  trailing negatives such as an option-event quantity printed as `20-`.
  Invalid quantity or closing-cash text is quarantined; no numeric parse
  failure becomes zero.
- Name-only fallback identities are explicitly unresolved. They may match one
  exact same-statement holding during staged resolution but cannot persist as
  invented ticker symbols.
- When the historical curated name fallback is applicable, it receives the
  row's native currency so it cannot collapse CAD/USD dual listings.
- A recognized Asset Review currency block declares a complete positions scope;
  a cash scope is complete only with a valid printed closing balance.
- Parsed transactions, positions, cash, and quarantines receive source spans,
  including coordinate/word evidence when extraction exposes it.

## Remaining limits

- RBC has historical column variants. A new debit/credit layout needs a
  fixture or source spot-check before its sign mapping is trusted.
- `TRANSFER TO/FROM` and ambiguous journals remain event-specific and must not
  be paired or balanced by parser invention.
- Complete parser scopes feed the residual engine. An approved re-ingest/shadow
  rebuild is still required before they improve the dated live ledger.

Fixtures cover one dual-currency monthly account, multi-page activity, option
transactions, cash, and annual-performance parsing. See
[PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for the shared output rules.

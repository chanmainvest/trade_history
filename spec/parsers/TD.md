# TD WebBroker parser

Implementation: `src/ledger/parsers/td.py`, parser name `td`, current version
`2.3.0`.

## Recognition and account shape

TD PDFs commonly contain separate `Direct Trading - CDN` and `Direct Trading
- US` subaccounts. They remain distinct accounts/currencies. Annual
`*_summary.pdf` files emit annual records rather than monthly holdings.

The parser splits every recognized legacy `Statement for <month> ...` header
and full `<month> <day>, <year> to ...` period header before account splitting.
It aggregates repeated page fragments for the same period/account/currency, so
bundled months and repeated headers emit one statement identity per logical
scope.

## State and evidence handling

- Holdings and activity sections retain account, currency, period, current
  section, and continuation state. An opening cash balance can carry across a
  repeated page fragment until its closing balance is found.
- Multi-line option holdings tolerate harmless page/header lines between their
  contract head and expiry/strike tail. The parser retains the printed option
  root, expiry, strike, type, and multiplier.
- Adjusted activity identities printed as `ROOT+$'YY MON@STRIKE` retain the
  printed root and option fields; a following signed contract quantity is
  parsed for expiration/exercise/assignment movements.
- Name-only buy/sell rows retain an unresolved printed identity instead of
  being discarded. TD execution references such as `RL-881589` and trailing
  `AS OF` annotations are removed from the identity term. The staged resolver
  can then match one exact same-statement holding (for example,
  `VELO3D INC-NEW` to printed symbol `VELO`); unmatched names keep a null
  persisted instrument rather than an invented ticker.
- Curated holding-name fallback respects native-currency listings for
  dual-listed securities.
- Buy/sell numeric tails are parsed as quantity, price, amount, and optional
  running balance. Digits embedded in a security name such as `VELO3D` or
  `12M` are never treated as the quantity.
- Stock splits map to the canonical `stock_split` type. Buy/sell, option
  buy/sell, known fees/taxes, and known income events receive canonical cash
  directions when TD prints an unsigned debit/credit amount.
- Missing/invalid quantities or closing cash values, and unrecognized numeric
  candidate rows, are quarantined rather than converted to zero.
- Recognized holdings/cash sections declare explicit scope completeness; cash
  requires a valid printed closing balance. Parsed rows and quarantines receive
  page/line source spans, with coordinates/words when available.

## Remaining limits

- New TD statement generations and non-standard pending rows require a fixture
  or PDF spot-check before their date and sign rules are trusted.
- A complete parser scope is not proof that a broker portfolio total or
  roll-forward reconciles; the engine records that calculation and source
  review remains necessary for any residual.
- Existing active/live TD rows were produced by earlier parser versions and
  require a reviewed re-ingest/shadow rebuild to gain these fixes.

Fixtures cover modern CDN/US holdings and options, legacy 2016–2017 bundled
months, full-header 2018–2022-style bundles, repeated account fragments,
closing cash, and source evidence. See
[PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for shared rules.

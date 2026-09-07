# TD WebBroker parser

Implementation: `src/ledger/parsers/td.py`, parser name `td`, current version
`2.9.0`.

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
- Equity holdings accept a signed printed quantity. A short row such as
  `-2,000 30.480 ...` retains quantity `-2,000` and market price `30.480`;
  the sign is not swallowed into the name or shifted into the price column.
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
- Explicit name/symbol/ticker-change activity can populate the v3 related-
  instrument contract when the row prints both symbols.
- Buy/sell numeric tails are parsed as quantity, price, amount, and optional
  running balance. Digits embedded in a security name such as `VELO3D` or
  `12M` are never treated as the quantity. Legacy rows that print quantity
  before the fund name use their separate leading-quantity grammar.
- Signed balances accept `-$...`; February options accept both TD's `FE` and
  `FB` month codes. Activity-line option tokens reject month/day combinations
  that do not map to a valid expiry. Activity verbs are case-insensitive. The
  current-period state machine stops at the printed pending-activity boundary.
- In-kind transfers retain their security quantity/instrument with zero cash.
  `Disposition`, Web Banking transfers, paper-statement fees, cheques,
  interest rebates, cash-in-lieu, and capital-gain distributions retain their
  printed cash effects.
- Month-end DRIP reinvestments print once, on the statement after their pay
  date: `<prior-month-end> Dividend <fund> <units> 0.00 <balance>` followed by
  `Reinvestment Plan VALUE = <n>`. The row is the only appearance of that
  dividend, so it is recorded on the printing statement as
  `reinvest_dividend` with the printed date (before the period), the printed
  units, and zero cash; the validator warns instead of failing on its date.
  Cash-carrying rows echoed from the prior period are duplicates and still
  quarantine as out-of-period.
- Stock splits map to the canonical `stock_split` type. Buy/sell, option
  buy/sell, known fees/taxes, and known income events receive canonical cash
  directions only when TD prints an unsigned debit/credit amount; a printed
  sign (for example negative debit interest on an income row) is source
  evidence and is preserved as printed.
- Missing/invalid quantities or closing cash values, and unrecognized numeric
  candidate rows, are quarantined rather than converted to zero. A cash scope
  containing an unsupported dated numeric event is `unknown`, not complete.
  Likewise, one unrecognized numeric holding row makes the entire positions
  scope `unknown`; readable holdings remain available, but reconciliation may
  not treat a partial table as a complete checkpoint.
- Recognized holdings/cash sections declare explicit scope completeness; cash
  requires a valid printed closing balance. Parsed rows and quarantines receive
  page/line source spans, with coordinates/words when available.
- Repeated account/currency fragments accumulate physical pages and cash state.
  Incomplete scopes emit structured blockers linked to the precise quarantine
  and evidence rows.
- Disclosure pages and pages explicitly headed for the other currency account
  are excluded from sub-statement page ownership.
- One-line holdings retain one evidence line; wrapped holdings and adjusted
  option contracts retain both printed lines for geometry.
- Account Summary opening/change/ending values plus closing equity and cash
  totals populate snapshot sets. Unprinted securities opening values are not
  inferred.
- Strict printed `TDB####`/`TDB####X` mutual-fund codes remain broker
  identifiers, so a complete holdings table is not discarded as a free-form
  unresolved name.
- The account summary captures `Beginning balance`, `Change in your account`,
  and `Ending balance` verbatim, including TD's minus-before-dollar form
  (`-$24,175.14`); a printed negative change stays negative so the
  statement-change continuity check verifies the printed arithmetic.
- A reverse split printed as a two-leg book swap (`Reverse Split NAME -100
  12,196.99 …` / `NAME CORP 98 -12,196.99 …`) carries the out/in share
  quantities as signed deltas: both legs are recorded as in-kind journals
  with their printed quantities and instruments, so the rollforward consumes
  the split without a ratio. A `0.00` split note instead prints the resulting
  total, which is not a delta — its quantity stays uncaptured under
  `stock_split`.
- `Security Position` rows (fractional-residue settlement journals printed
  with `0.00` cash and an internal vehicle name, e.g. `RBC QUBE CDN`) are
  classified as non-position adjustments; they complete the cash activity
  without claiming a security movement.
- Interest-period memos (`Interest INTEREST TO JUL 16 -87.81`) are cash
  events that never name a security: they are stored deliberately without an
  instrument and without the unresolved-identity marker.
- Activity option tokens retain OCC adjusted roots, including digit-led
  roots (`5SOXS`) and the `+$` adjustment marker (`98TRI+$`, `BABA+$`), so
  activity rows join the holdings-table instrument for the same contract.
- Holdings wrap symbols for broker fund codes carry `printed_fund_code`
  identity for the full `RBF|TDB` code family, not just `TDB####`.
- `Stock Exchange` rows are in-kind unit exchanges between broker-pooled
  funds (signed quantities, `0.00` cash); the pooled funds resolve through
  the reviewed name catalog (`RBC QUBE CDN` → `RBF678C`,
  `RBC OSH CD` → `RBF610C`) from the holdings rows that print the codes.
- Mutual-fund activity names resolve through the reviewed name catalog
  (`TD CDN EQ-D` → `TDB3089C`, `TD DIV INCM-D` → `TDB3087C`) whose entries
  pair the printed name and `TDB####` code on the same holdings row; a
  catalog hit on a strict broker code carries `printed_fund_code` identity.
  Abbreviated transfer names resolve too (`GLB X US DOLL CURR-A ETF` →
  `DLR`/`DLR.U`), as do abbreviated equity dividend names whose holdings
  rows wrap the symbol on the same page (`CANADIAN PAC KANSAS` → `CP`,
  `CDN IMPERIAL BK` → `CM`).

## Remaining limits

- New TD statement generations and non-standard pending rows require a fixture
  or PDF spot-check before their date and sign rules are trusted.
- A complete parser scope is not proof that a broker portfolio total or
  roll-forward reconciles; the engine records that calculation and source
  review remains necessary for any residual.
- Existing active/live TD rows were produced by earlier parser versions and
  require a reviewed re-ingest/shadow rebuild to gain these fixes.

## Annual performance report (2.9.0)

December monthly statements and the `*_summary.pdf` files attach
"Your performance report" and "Your fees and charges report" pages for the
calendar year (one performance page per account currency). Those pages are
their own category with their own reconciliation: the parser excludes them
from the monthly statement's pages — their "January 1 to December 31"
headers would otherwise create bogus year-long monthly statements — and
extracts them as a separate annual statement (`statement_type = annual`)
whose `annual_performance_reports` rows carry the printed performance table
(beginning balance, deposits, withdrawals, change in value, ending balance,
the since-inception date, and the personal rates of return). Summary-sourced
annual statements gain the same rows; a year with both a summary file and a
December statement therefore has two source-distinct annual records that
print the same numbers. Fee lines stay evidence lines on the annual record.

## Legacy 77FF49-era shapes (2.8.7-2.8.8)

The 2016-2018 quarterly statements print several shapes the modern grammar
does not, all verified against printed pages:

- Leader-dot separator rows between holdings rows are pure decoration.
  Extraction drops period glyphs belonging to visual rows that are almost
  entirely dots (`pdf_text._without_leader_dot_rows`), so decimals in
  content rows survive while the interleaved dots disappear.
- 2016 holdings print `<qty> Seg <NAME> <SYM> <price|N/D> <book>
  <market|N/D> <pct>` (legacy grammar); 2017-2018 holdings print the name
  first with an extra unrealized-gain column (`<NAME> <qty> SEG <price|N/D>
  <book> <market|N/D> <gain> <pct>%`). N/D is a printed no-value marker:
  the position persists with those cells uncaptured. Rows whose ticker
  prints nowhere (defunct NORTEL/BATTERY) have no printed identity and
  stay quarantined.
- A printed FundServ code (`TDB###`/`RBF###`) types its holding row as a
  mutual fund even when no recognized section header precedes it.
- Footnote lines (`4U=US dollars`, `4Book costs/values are converted ...`,
  `4The US dollar conversion rate ...`), squashed portfolio totals
  (`Totalportfolio ...`), and repeated account headers are furniture.
- Activity dates may print squashed (`Dec31 Dividend`); Web Banking rows
  split across two lines with the direction verb, transfer reference,
  amount, and running balance on the continuation (`Deposit RX... 10,000.00
  70,111.90`), which promotes the amount and fixes the direction; DRIP
  rows split the same way (`Dec31 Dividend` over `Reinvestment Plan 0.553
  <FUND> 0.00 <balance>` and a separate `VALUE= 5.53` line).
- Fund-series exchange legs print quantity, transferred value, and running
  balance (`Exchange TD CDN EQ-D /NL'FRAC 2,379.892 -22,516.16 21,288.30`);
  pairs net to zero cash. `Defunct Security` removes a worthless security
  in kind at the printed 0.00 price.

Fixtures cover modern CDN/US holdings and options, legacy 2016–2017 bundled
months, full-header 2018–2022-style bundles, repeated account fragments,
closing cash, and source evidence; `td/legacy_seg_holdings.txt` covers the
legacy Seg grammars and the exchange/defunct/Web-Banking/DRIP shapes. See
[PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for shared rules.

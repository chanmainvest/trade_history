# CIBC parser

Implementation: `src/ledger/parsers/cibc.py`, parser name `cibc`, current
version `2.9.0`.

## Recognition and account shape

The parser handles the configured CIBC Imperial Service, Investor's Edge, and
TFSA folders/text. It extracts `###-#####` account numbers from text or a
filename and infers account type from statement labels. Tax-document PDFs are
returned as skipped/empty rather than monthly brokerage statements.

One monthly PDF produces one account/period statement. Its CAD and USD
activity/portfolio sections are retained as native-currency child rows with
separate positions and cash snapshot scopes.

## State and evidence handling

- English month ranges establish the period; Canadian/U.S. headings switch the
  current currency and portfolio/activity section.
- A no-date activity line is never assigned blindly to the preceding
  transaction. Numeric/activity-like lines are quarantined, which keeps page
  headers and unrelated fund/corporate-action text out of transfer evidence.
  The per-page furniture CIBC prints on every page — the statement's own
  period line ("July 1-July 31, 2026"), the "(previous statement …)" marker,
  and the page footer that starts with a deposit number before "account #" —
  is recognized structurally and skipped rather than quarantined. Disclosure
  and legal footer prose (fee announcements, GST/HST/QST registration,
  reserved-rights text, the CIBC World Markets address and toll-free line,
  `HRI-*` printer barcodes) is likewise skipped inside portfolio sections;
  those fragments are matched by fixed legal markers, never by amounts.
- Account-transfer direction words followed by an account number (`TO` and
  `FROM`) are not instruments or tickers.
- An en dash immediately before a money value is normalized as a negative
  sign, while an em dash remains a blank-cell marker. In-kind transfer and
  unpriced option-event quantities are read from the printed cell before those
  markers. If an option event prints only a strike, its quantity remains null
  and the row is quarantined for review.
- Equity tickers can be parenthesized; options retain CIBC's printed
  `CALL/PUT .ROOT MON DD YYYY STRIKE` identity. Option buy/sell refinement
  requires `\b(CALL|PUT)\b` tokens and an explicit `OPEN CONTRACT` phrase so
  issuer names containing `OPEN` stay equity. Mutual funds can remain
  printed-name identities pending a reviewed fund-code lookup. The staged
  resolver either proves the identity or removes the pseudo-token before
  persistence.
- Two deviant printed shapes of the "Other" option-holding row also parse
  (2.8.5, reviewed against the 588-93738 Feb-Apr 2026 silver/gold rows).
  First, when both quantity and book value are negative, CIBC may print the
  two columns flush together with no separating space
  (`PUT GLD JUN 18 2026 340 -200-$127,743.05 ...`); the row splits at the
  minus sign, the only valid tokenization. Second, a row whose strike column
  wrapped onto the following continuation line prints the quantity in the
  strike slot (`PUT SLV APR 17 2026 25 $17,463.20 5.400 $13,500.00` over
  `70.50 ISHARES SILVER SHARES`); the account-owner review fixed the
  reading — inline number = quantity (pinned by the printed market value:
  5.400 × 25 × 100 = $13,500.00), wrapped number = strike, echoed
  identically under the Feb 17 `Bought` activity row. The strike-bearing
  wrap is consumed with its row; a strikeless row whose strike prints
  nowhere stays quarantined.
- The same split-description shape exists in Account Activity, where the
  option row's contract description wraps its strike onto the continuation
  line under the row (2.8.6, corpus-wide: `Bought/Sold PUT <ROOT> MON DD
  YYYY <qty> <price> <amount>` over `52.50 BHP GROUP LIMITED ADS`, and
  unpriced `Expired PUT <ROOT> MON DD YYYY -25 — —` the same way). The
  priced variant is gated on the strike line printing below the row; the
  unpriced variant additionally requires a negative quantity, because a
  strike never prints negative — `RE_OPT_EVENT`'s strike-only reading of a
  single positive number is untouched. Both join the strike line into the
  row's description and consume it, so it no longer quarantines as an
  activity-like fragment. Activity rows whose strike prints nowhere keep
  the prior handling (`option identity is incomplete`). Recovered 14
  transactions across 2021-2023 and Feb/Apr 2026; each was spot-checked
  against the printed continuation line.
- Curated name fallback respects native-currency listings for dual-listed
  securities (for example, Barrick `ABX` in CAD and `GOLD` in USD).
- `EFT DEBIT BANK ACCOUNT` is cash entering the brokerage account. Signed
  `Contrib TRANSFER TO/FROM` rows retain their account-transfer direction and
  never turn `TO`/`FROM` into an instrument. The account reference's journal
  suffix (`TRANSFER FROM 588-58964-22`) is masked before number-tail
  extraction so it cannot be read as a negative quantity: such a row is a
  cash transfer, not a position movement. A dated row with two explicit
  blank security cells and a signed cash value is retained as a generic
  adjustment when CIBC prints no activity subtype.
- Explicit name/symbol/ticker-change verbs are retained for the shared v3
  contract; only a printed `FROM <old> TO <new>` pair becomes a dated lineage.
- Wrapped security-name lines and the printed ``(SYM/EXCH)`` ticker
  continuation line under a row are merged into that row's identity, so a
  holding like `VANECK ETF TRUST` + `(NLR/US)` resolves to its printed
  symbol; a printed "… ETF" name types the row as an ETF even when the
  issuer contains "FUND" (e.g. `SPROTT FUNDS TRUST`); digit-free
  corporate-action note lines are absorbed as description evidence.
  Digit-bearing note sub-lines under a row attach to it as well when they
  match the keyword-anchored note vocabulary — `TRANSFER TO`/`FROM <acct>`,
  `REC`/`PAY` dividend dates,
  `CASH DIV ON <n> SHS`, `DIST ON <n> SHS`, `REINVESTED DIV @ <price>`
  (plus bare `@ <price>` continuation lines), `FA`/`ADJ` split ratios, and
  `VALUE $…` value/par text (e.g. `VALUE $0.001 PER SHARE`), the
  option-assignment underlying CUSIP wrap (`HECLA MINING CO A/E 9GDQHF6 2`,
  split as `HECLA MINING CO` + `A/E 9GDQHF6 35`), and the bare contract echo
  CIBC reprints under `ASSIGNMENT OF OPTION` note lines
  (`PUT HL JUN 18 2026 22`); they may fuse with the wrapped name's last word
  (`CORP COM CASH DIV ON 3500 SHS`).
  The lines are row-local evidence, never new rows: activity rows begin
  with a `Mon DD` date prefix and portfolio rows end in a numeric column
  tail. Any other digit-bearing line without a date prefix still
  quarantines as `unclaimed activity-like row`.
- The CAD-converted presentation total `Total closing cash balance in
  Canadian dollars` printed under the currency-conversion footer is
  furniture: FX conversion is presentation-only and the native-currency
  closing balance is recorded from the dated closing-balance row. The
  squeezed wire-confirmation receipt fragments printed under large wire-
  settled fund redemptions — `1000THS=700,WIRE=AAMX0FL` and
  `GA=288662.60; TF=6.95` (wire reference, gross amount, transfer fee) —
  are the same furniture class: they repeat the settled row's proceeds and
  carry no date or row shape, so they are skipped rather than quarantined.
- The pdfplumber `ƒ` glyph (a "book cost estimated from market value"
  footnote marker embedded in portfolio rows) is stripped before column
  parsing, like the older `ð` em-dash artifact.
- Corporate-action verbs from the 2026 Investor's Edge layout: printed
  `Assignment` (noun form of an assigned option event), `Shrs in xc`
  (option-contract exchange after a corporate action — a `journal` movement
  with ±printed contracts and blank price/amount cells), and `Merger` legs
  (quantity, blank price cell, signed cash value). Adjusted option roots may
  carry digits (for example `SOXS1` after a reverse split). The plain stock/
  dividend verb path knows `Shrs in xc` too, so a mutual-fund residual
  flatten (`Shrs in xc 1000THS <FUND> -2 — —` with wrapped fund-name
  continuations) records a `journal` movement of the printed units: its
  identity stays unresolvable (no tradable symbol), so the transaction is
  persisted with `resolution_method = unresolved_printed_identity` instead
  of either dropping the movement or inventing a token. The printed
  `1000THS` qualifier states the quantity's unit — thousandths of a fund
  unit (2.8.7) — so the printed `-2` records as `-0.002`: the dust a full
  redemption leaves behind, exactly the balance the flatten clears
  (`1000THS=639` in the wire footer is the same shorthand).
- A dividend whose note line prints a reinvest price with a blank cash cell
  (`REINVESTED DIV @ 15.7635`, or the squeezed legacy `REINVEST. JAN 29 2016
  @ 9.0419` shape) is an in-kind reinvestment: it is recorded as
  `reinvest_dividend` with the printed share count, not as a cash dividend.
  A printed amount keeps the row a cash dividend regardless of its notes.
- `NON-RES TAX WITHHELD` is a fixed label with no security columns (the em
  dashes are the blank symbol cells): the row is a pure cash event and is
  recorded without an unresolved-identity marker. Tax rows that do print a
  security name keep resolving it.
- Bare-name rows respect the printed native-listing symbol (2.8.8): First
  Majestic renamed its TSX ticker `FR` → `AG`, so a bare `FIRST MAJESTIC
  SILVER CORP` row in a CAD account resolves to `AG` — the symbol the
  current holdings rows print as `(AG/TSX)` — instead of a dead legacy
  listing. The 2021-2022 `FR` era lives on its own identity through the
  same-statement holding of those statements.
- Year-end return-of-capital tax disclosure is furniture, not a transaction
  (2.8.9): `Rtn of Cap <FUND> — — —` prints all three data cells blank and
  is followed by `CL F RTN OF CAPITAL YEAREND` / `VALUE <amt>` per-unit
  history lines; every rollforward reconciles without them. A cash
  return-of-capital row (amount printed, cells otherwise blank) still
  records as an adjustment, with its component note lines
  (`ETF RTN OF CAPTL n SHS`, `REC/PAY <date>`) merged into the description;
  dividend component notes (`L/T CAP GNS n SHS`) ride with their dividend
  the same way. Bare digit runs under a dated row are wire/transfer
  confirmation numbers — receipt furniture. Registered-account book-value
  boilerplate whose leading printer-barcode fragment (`RT0001.`) fuses onto
  the sentence is skipped by its prose text.
- A recognized portfolio section is declared `complete`; a cash scope is
  complete only after a valid printed closing balance and no unsupported dated
  numeric activity. Invalid/missing numeric fields and unclaimed numeric rows
  are quarantined, never stored as zero.
- Parsed transactions, positions, cash, and quarantine rows receive
  page/line source spans, with bounding boxes/words when PDF extraction
  supplied them.
- The statement explicitly owns its physical source pages; incomplete scopes
  carry structured blocking issues linked to evidence/quarantine.

## Year-end Account Report (2.9.0)

The December eStatement attaches a four-page "Your Year-end Account Report"
after the monthly statement: a cover page, "The Performance of Your
Investment Account #<acct>", "The Cost of Your Investment Account", and an
information page. The report is its own category with its own
reconciliation, so the parser extracts it as a separate annual statement
(`statement_type = annual`, period January 1–December 31) whose pages are
excluded from the monthly statement, and its printed lines and numbers are
stored on that annual record (`annual_performance_reports`) rather than
mixed into monthly transactions or holdings.

The performance page prints one row per line with either one value
(single-period reports) or two (the report year and since inception); the
stored figure is the report-year column, and the since-inception date comes
from the `... * (CAD)` column header. The "Change in Value" row maps to
`net_investment_return`; the "Per Year" percentages map to the
money-weighted returns (first = 1 year, last = since, third = 3 years when
printed). The cost page's fee lines remain evidence lines on the annual
statement — no monthly transaction or quarantine is created from the
report. December PDFs 2021-2025 all carry the report.

## Remaining limits

- CIBC text can contain `ð`/dash artifacts and unusually wrapped descriptions.
- Disclosure-only pages with no Account Activity or Portfolio Assets section
  are excluded from parsing and statement page ownership.
- A new debit/credit layout still needs a fixture or PDF spot-check before its
  event sign mapping is trusted.
- Section completeness is evidence of a recognized printed section, not yet a
  reconciliation against every printed portfolio total.

Fixtures cover dual currencies, options, funds, EFT/contribution cash rows,
unlabelled signed adjustments, incomplete cash scopes, source evidence, a
TFSA option holding, the 2026 corporate-action layout (`Shrs in xc`,
`Assignment`, `Merger`, wrapped ticker continuations, the `ƒ` glyph), and
the residual shapes (fund residual flatten, wire-confirmation footer,
`NON-RES TAX WITHHELD`, reinvested dividends, option CUSIP/contract-echo
wraps). See [PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for the shared
output rules.

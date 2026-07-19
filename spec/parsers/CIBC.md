# CIBC parser

Implementation: `src/ledger/parsers/cibc.py`, parser name `cibc`, current
version `2.5.0`.

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
- Account-transfer direction words followed by an account number (`TO` and
  `FROM`) are not instruments or tickers.
- An en dash immediately before a money value is normalized as a negative
  sign, while an em dash remains a blank-cell marker. In-kind transfer and
  unpriced option-event quantities are read from the printed cell before those
  markers. If an option event prints only a strike, its quantity remains null
  and the row is quarantined for review.
- Equity tickers can be parenthesized; options retain CIBC's printed
  `CALL/PUT .ROOT MON DD YYYY STRIKE` identity. Mutual funds can remain
  printed-name identities pending a reviewed fund-code lookup. The staged
  resolver either proves the identity or removes the pseudo-token before
  persistence.
- Curated name fallback respects native-currency listings for dual-listed
  securities (for example, Barrick `ABX` in CAD and `GOLD` in USD).
- `EFT DEBIT BANK ACCOUNT` is cash entering the brokerage account. Signed
  `Contrib TRANSFER TO/FROM` rows retain their account-transfer direction and
  never turn `TO`/`FROM` into an instrument. A dated row with two explicit
  blank security cells and a signed cash value is retained as a generic
  adjustment when CIBC prints no activity subtype.
- Explicit name/symbol/ticker-change verbs are retained for the shared v3
  contract; only a printed `FROM <old> TO <new>` pair becomes a dated lineage.
- A recognized portfolio section is declared `complete`; a cash scope is
  complete only after a valid printed closing balance and no unsupported dated
  numeric activity. Invalid/missing numeric fields and unclaimed numeric rows
  are quarantined, never stored as zero.
- Parsed transactions, positions, cash, and quarantine rows receive
  page/line source spans, with bounding boxes/words when PDF extraction
  supplied them.

## Remaining limits

- CIBC text can contain `ð`/dash artifacts and unusually wrapped descriptions.
- A new debit/credit layout still needs a fixture or PDF spot-check before its
  event sign mapping is trusted.
- Section completeness is evidence of a recognized printed section, not yet a
  reconciliation against every printed portfolio total.

Fixtures cover dual currencies, options, funds, EFT/contribution cash rows,
unlabelled signed adjustments, incomplete cash scopes, source evidence, and a
TFSA option holding. See [PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for the
shared output rules.

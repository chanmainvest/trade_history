# CIBC parser

Implementation: `src/ledger/parsers/cibc.py`, parser name `cibc`, current
version `2.0.0`.

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
- Activity continuation lines extend the current transaction. Repeated headers
  and known footers are ignored rather than treated as verbs.
- Equity tickers can be parenthesized; options retain CIBC's printed
  `CALL/PUT .ROOT MON DD YYYY STRIKE` identity. Mutual funds can remain
  printed-name instruments pending a reviewed fund-code lookup.
- A recognized portfolio section is declared `complete`; a cash scope is
  complete only after a valid printed closing balance. Invalid/missing numeric
  fields and unclaimed numeric rows are quarantined, never stored as zero.
- Parsed transactions, positions, cash, and quarantine rows receive
  page/line source spans, with bounding boxes/words when PDF extraction
  supplied them.

## Remaining limits

- CIBC text can contain `ð`/dash artifacts and unusually wrapped descriptions.
- A new debit/credit layout still needs a fixture or PDF spot-check before its
  event sign mapping is trusted.
- Section completeness is evidence of a recognized printed section, not yet a
  reconciliation against every printed portfolio total.

Fixtures cover dual currencies, options, funds, cash, source evidence, and a
TFSA option holding. See [PARSER-CONTRACT.md](../PARSER-CONTRACT.md) for the
shared output rules.

# CIBC parser

Implementation: `src/ledger/parsers/cibc.py`, parser name `cibc`, current
version `1.0.0`.

## Recognition and accounts

The parser handles the configured CIBC Imperial Service, Investor's Edge, and
TFSA folders/text. It extracts `###-#####` account numbers from text or
filename and infers account type from statement labels. Tax-document PDFs are
returned as skipped/empty rather than parsed as monthly brokerage statements.

A statement is one account/period with CAD and USD activity/portfolio sections
combined into native-currency child rows. The current data model does not store
explicit section scope/completeness.

## Layout handling

- Periods use English month names/ranges.
- Activity and Portfolio Assets sections are split by Canadian/U.S. headings.
- The parser stitches activity continuation lines and ignores some repeated
  continuation headers.
- Equity tickers may be printed in parentheses; options use CIBC's
  `CALL/PUT .ROOT MON DD YYYY STRIKE` representation.
- Mutual funds can remain printed-name instruments pending reviewed fund-code
  lookup; do not hardcode a code.

## Known risks

- PDF text can contain `ð`/dash artifacts and multi-line descriptions.
- Repeated headers/footers can be appended to an activity description.
- Plain text loses debit/credit column geometry in some layouts.
- Cash closing values use `parsed_value or 0.0`; invalid text can become zero.
- Holdings quantities also have zero fallbacks rather than quarantine.
- Missing holding lines can be silently skipped, and source page/coordinates
  are not emitted.

Any refactor must use layout words/coordinates where column meaning controls
sign, preserve both currency sections, and add fixtures for normal/continued,
dual-currency, options, and mutual-fund layouts.

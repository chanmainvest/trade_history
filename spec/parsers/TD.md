# TD WebBroker parser

Implementation: `src/ledger/parsers/td.py`, parser name `td`, current version
`1.0.0`.

## Recognition and accounts

TD PDFs commonly contain separate `Direct Trading - CDN` and `Direct Trading
- US` subaccounts. These are real broker currency subaccounts and are emitted
as distinct accounts/currencies. Annual `*_summary.pdf` files emit annual
records rather than monthly holdings.

The parser handles modern activity/holding formats, multi-line option tails,
and a special legacy 2016–2017 `Statement for January 1 to ...` splitter.

## Critical current defect

The bundled-period splitter recognizes only the legacy `Statement for` header.
Many 2018–2022 quarterly PDFs contain repeated full period headers in another
form. They are parsed under the first period, emit repeated statement keys, and
later segments overwrite earlier ones. The corpus audit found 34 affected TD
bundles, 100 overwritten segments, and 470 transactions outside the stored
period.

Every period must be split before currency subaccounts, and duplicate
source/account/period output must fail validation before persistence.

## Other known risks

- Option positions can span description/root and expiry-tail lines.
- Holdings may place the symbol on the following line.
- Quantity fallbacks convert failed parsing to zero.
- Activity amounts/signs and header contamination need layout-aware parsing.
- The action map contains legacy labels that should be validated against the
  canonical transaction vocabulary.
- No section completeness or source coordinates are emitted.

Fixtures must cover modern monthly statements, 2016–2017 legacy bundles,
2018–2022 quarterly bundles, CDN/US subaccounts, options, and closing cash.

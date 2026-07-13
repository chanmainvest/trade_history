# HSBC parser

Implementation: `src/ledger/parsers/hsbc.py`, parser name `hsbc`, current
version `1.0.0`.

## Recognition and accounts

HSBC PDFs can contain several account sections. The parser recognizes account
headers, emits one statement per account, and infers currency from the printed
type label or the observed `-E` (CAD) / `-F` (USD) suffix convention. Annual fee
summaries emit an annual record with no fabricated monthly holdings.

## Layout handling

- Normalization repairs lost spaces around compact dates.
- Activity rows often look like `Sep5 Dividend ...`.
- Compact options use forms such as `PUT-100TLT'2616JA@75`.
- Holdings include explicit/parenthesized symbols when available; adjacent
  continuation sections for the same account are combined by the parser.
- Parentheses/trailing formatting are interpreted through shared money parsing.

## Known risks

- First-word/ticker heuristics and multi-line holdings can choose an incorrect
  synthetic identity.
- Continuation correctness depends on adjacent text order rather than explicit
  stored section scope.
- Quantity and closing-cash parse failures can become zero.
- Text extraction lacks page/word evidence and section completeness.
- Activity sign/amount coverage has not passed full cash reconciliation.

Refactoring must make account continuation a state-machine transition, retain
the printed account/currency evidence, and test parentheses negatives,
continued pages, compact options, and every observed account-header variant.

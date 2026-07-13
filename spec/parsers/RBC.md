# RBC parser

Implementation: `src/ledger/parsers/rbc.py`, parser name `rbc`, current version
`1.0.0`.

## Recognition and formats

The parser handles monthly Direct Investing statements and annual investment
performance reports. Monthly text is split at `CDN $` / `U.S. $` account
blocks. Annual reports emit annual statement summaries for available CAD/USD
money-weighted return sections and do not create monthly movements/positions.

Options use RBC's printed call/put, root, expiry, strike, multiplier/quantity
layout. Activity verbs are mapped to the common transaction vocabulary.

## Critical current defect

For a monthly PDF, CAD and USD blocks are emitted as separate
`ParsedStatement` objects with the same account and period. The SQLite key is
`(source_file_id, account_id, period_end)`, so the second block rewrites the
statement and deletes the first block's children. The live audit found no RBC
monthly statement retaining both position currencies; recent rows generally
retain only USD.

The fix is not to invent separate broker accounts. The target model is one
physical account/period statement with explicit CAD/USD child scopes.

## Other known risks

- Plain extracted text loses debit/credit column meaning, so cash signs are not
  defensible for every activity layout.
- Trailing negative values and `TRANSFER TO/FROM` wording require event-aware
  direction handling.
- Quantity and closing-cash parse failures can become zero.
- Unrecognized holding rows may be skipped without occurrence-level quarantine.
- Period parsing and page continuations need fixtures across format generations.

The parser must be refactored with word coordinates/state, validated unique
statement output, both currency scopes, and cash/position roll-forward tests
before rebuilding the ledger.

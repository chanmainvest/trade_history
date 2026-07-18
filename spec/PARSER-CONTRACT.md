# Parser contract

This page owns the common parser interface and semantic rules, including the
runtime validation boundary used before persistence.

## Current interface

A registered parser exposes:

```python
NAME: str
VERSION: str
can_handle(folder_name: str, first_page_text: str) -> bool
parse(pdf: PdfText) -> ParseResult
```

The registry tests parsers in registration order and selects the first match.
Exceptions from `can_handle()` are logged and selection continues.

`ParseResult` contains parser name/version, zero or more `ParsedStatement`
objects, string errors, and a `parsed`/`skipped` status. A skipped result has a
reason but no fatal parser error and is never activated. A statement contains
account, period, type, transactions, positions, cash balances,
annual-performance rows, quarantine
items, and optional `ParsedSnapshotSet` declarations. Legacy `(raw_line,
reason)` quarantine tuples remain accepted during the parser migration.

Exact fields and the `TxnType` literal vocabulary are defined in
`src/ledger/parsers/types.py`. `parsers/validation.py` enforces the runtime
contract on the complete `ParseResult` before staged ingestion writes any
statement children.

## Required semantics

- Parsing is deterministic and side-effect free: no database writes or network.
- Every statement has a defensible account and ISO period.
- Every monetary/position row carries native currency.
- Every recognized row preserves the printed description/raw evidence.
- A position-affecting transaction has an instrument or is quarantined.
- An option retains root, expiry, strike, call/put, currency, and multiplier.
- Missing or invalid numeric text is `None`/quarantine, never zero by fallback.
- Transaction signs represent the printed/economic event consistently; no
  consumer should need institution-specific sign guesses.
- Output statement identities are unique within a source.
- A parser declares the scope and completeness of every holdings/cash section.
- A parser preserves its printed instrument identity; it does not need to guess
  a public ticker from an uncertain free-form name.

Unique output identity, date/type/currency/option validity, finite numbers,
declared scope validity, and source-span shape are enforced now. Correct
economic signs cannot be proven from a dataclass alone. An emitted
positions/cash scope without a declaration remains a warning and is persisted
as `unknown`, never as complete.

## Transaction vocabulary

The current literals cover buys/sells/shorts, four option open/close actions,
assignment/exercise/expiration, income/interest, transfers/journals,
deposits/withdrawals, taxes/fees/FX/adjustments, reinvestment, splits, and
corporate actions. Add a new value only with parser, quantity/cash semantics,
schema/docs, API, and tests updated together.

## Evidence and quarantine

`SourceSpan` can carry raw text, page/line, bounding box, words, and parser
rule. Transactions, positions, cash balances, snapshot sets, and the richer
quarantine type can carry one. The writer assigns every parsed/quarantined row
a deterministic evidence record, using a stable row occurrence when layout
coordinates are not yet available. Cash balances now carry their opening/
closing source line(s).

The validator reports missing cash evidence and undeclared row scopes as
explicit warnings. It treats malformed dates/currencies/numerics, invalid
transaction vocabulary, incomplete options, duplicate statement identities,
invalid snapshot declarations, and parser-reported errors as fatal.

`PdfText` now retains raw page text plus `PdfWord`/`PdfLine` layout rows when
`pdfplumber` exposes coordinates. The parser bridge normalizes text only for
matching; it keeps the original raw evidence in the stored span. Text fixtures
and the `pypdf` fallback still receive deterministic page/line evidence but no
invented bounding box or word coordinates.

The four bank parsers are at layout/state-machine version 2. They attach spans
to transactions, positions, cash, and quarantine rows, and declare a scope
`complete` only after recognizing the full relevant section (and, for cash, a
valid printed closing balance). An unrecognized or incomplete section remains
`unknown` or is quarantined; it never clears a prior checkpoint by assumption.
Rows outside a statement's declared period or with an incomplete option
contract are likewise quarantined until the model can represent the variant
(for example, a pending transaction) without guessing.

## Staged identity resolution

`ParsedInstrument` can carry a resolution method/confidence/evidence in
addition to its parsed identity. During Phase 3 ingestion, the resolver records
one of these outcomes without calling the broad name-to-ticker repair map:

1. a complete option contract or explicit printed symbol is retained;
2. an exact reviewed alias or resolved reviewed fund lookup is applied;
3. one exact same-statement holding identity is applied; or
4. the printed identity remains unresolved with confidence `0.0`.

For transactions the selected method, confidence, and available source-span
evidence are persisted in `transactions`. Holdings retain regular source
evidence and instrument-level provenance. An uncertain parser output must stay
unresolved/audited, never become a guessed ticker.

After all active sources are available, reconciliation may attach an unresolved
buy/sell to one uniquely matching equity/ETF name already observed in a
same-currency checkpoint. That derived corpus match is not parser output and is
owned by [RECONCILIATION.md](RECONCILIATION.md).

## Known contract violations

- The database still accepts arbitrary transaction text, although new parser
  output is checked against `TxnType` before persistence.
- Layout coordinates are preserved when available, but the bank parsers still
  use broker-specific text state machines rather than a general table engine.
  Debit/credit coverage must be spot-checked for each new layout.
- A `complete` declaration proves a recognized printed section and valid parsed
  rows; it is not yet independently validated against every broker total.
- Existing active/live runs predate parser v2. They retain their historical
  evidence, scope, and numeric quality until a reviewed re-ingest/shadow
  rebuild is approved.

## Test requirements

Tests use committed synthetic fixtures under `tests/fixtures/`, not ignored
private text dumps. The initial corpus covers all four institutions, dual
currencies/accounts, options, funds, annual reports, legacy TD splitting, and
the known RBC/TD collision formats. Each materially distinct layout needs coverage
for statement splitting, currencies, signs, instruments/options, positions,
cash, quarantine, and source evidence. Real PDFs remain private and read-only;
full-corpus audits are local acceptance checks.

`tests/test_refactor_acceptance.py` keeps the previously failing collision and
zero-fallback cases as ordinary regressions once a phase implements them.

See institution files under `spec/parsers/` and cross-cutting lessons in
[EXTRACTION-CORNER-CASES.md](EXTRACTION-CORNER-CASES.md).

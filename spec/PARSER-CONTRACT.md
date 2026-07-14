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
objects, and string errors. A statement contains account, period, type,
transactions, positions, cash balances, annual-performance rows, quarantine
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

Parser v1 implementations currently mostly supply raw lines and deterministic
occurrences; Phase 4 must supply page/column coordinates and explicit
complete/partial section declarations from layout-aware state machines.

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

## Known contract violations

- All four bank parsers contain quantity `parsed or 0.0` paths; CIBC/RBC/HSBC
  also contain cash closing-balance fallbacks to zero.
- Existing parser implementations have not yet declared child currency/section
  scopes or proved completeness, even though the type can express them.
- The database still accepts arbitrary transaction text, although new parser
  output is checked against `TxnType` before persistence.
- Plain text extraction loses column geometry needed for defensible debit/
  credit signs in some layouts.

## Test requirements

Tests use committed synthetic fixtures under `tests/fixtures/`, not ignored
private text dumps. The initial corpus covers all four institutions, dual
currencies/accounts, options, funds, annual reports, legacy TD splitting, and
the known RBC/TD collision formats. Each materially distinct layout needs coverage
for statement splitting, currencies, signs, instruments/options, positions,
cash, quarantine, and source evidence. Real PDFs remain private and read-only;
full-corpus audits are local acceptance checks.

`tests/test_refactor_acceptance.py` uses strict xfails for later-phase target
behavior. An unexpected pass fails the suite until the marker is removed and
the fixed behavior becomes a normal regression.

See institution files under `spec/parsers/` and cross-cutting lessons in
[EXTRACTION-CORNER-CASES.md](EXTRACTION-CORNER-CASES.md).

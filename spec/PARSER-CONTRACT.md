# Parser contract

This page owns the common parser interface and semantic rules. It distinguishes
the Python types that exist today from validation that still needs to be built.

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
transactions, positions, cash balances, annual-performance rows, and
`(raw_line, reason)` quarantine tuples.

Exact fields and the `TxnType` literal vocabulary are defined in
`src/ledger/parsers/types.py`. Runtime validation of the literal vocabulary is
not currently performed.

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

The last three requirements are design contracts, not guarantees of the
current dataclasses/pipeline.

## Transaction vocabulary

The current literals cover buys/sells/shorts, four option open/close actions,
assignment/exercise/expiration, income/interest, transfers/journals,
deposits/withdrawals, taxes/fees/FX/adjustments, reinvestment, splits, and
corporate actions. Add a new value only with parser, quantity/cash semantics,
schema/docs, API, and tests updated together.

## Evidence and quarantine

Today, transactions and positions can store `raw_line`; quarantine stores a
raw line and reason. Cash and annual rows lack raw evidence, and no common type
stores page, occurrence, bounding box, or parser rule. Verify therefore
fuzzy-matches normalized raw strings to freshly extracted PDF lines.

Target evidence is a deterministic source-row key plus source fingerprint,
page, occurrence, raw text, coordinates when available, and parser rule/version.

## Known contract violations

- All four bank parsers contain quantity `parsed or 0.0` paths; CIBC/RBC/HSBC
  also contain cash closing-balance fallbacks to zero.
- The parser type cannot express a child currency/section scope or whether a
  snapshot is complete.
- The pipeline accepts duplicate statement identities without preflight.
- `TxnType` is type-checker-only and the database accepts arbitrary text.
- Plain text extraction loses column geometry needed for defensible debit/
  credit signs in some layouts.

## Test requirements

Tests must use committed redacted/synthetic fixtures under `tests/fixtures/`,
not ignored private text dumps. Each materially distinct layout needs coverage
for statement splitting, currencies, signs, instruments/options, positions,
cash, quarantine, and source evidence. Real PDFs remain private and read-only;
full-corpus audits are local acceptance checks.

See institution files under `spec/parsers/` and cross-cutting lessons in
[EXTRACTION-CORNER-CASES.md](EXTRACTION-CORNER-CASES.md).

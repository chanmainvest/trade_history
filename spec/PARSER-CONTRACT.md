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
items, explicit physical `page_numbers`, and optional `ParsedSnapshotSet`
declarations. Legacy `(raw_line,
reason)` quarantine tuples remain accepted during the parser migration.

Exact fields and the `TxnType` literal vocabulary are defined in
`src/ledger/parsers/types.py`. `parsers/validation.py` enforces the runtime
contract on the complete `ParseResult` before staged ingestion writes any
statement children.

The active parser contract is version `6`. It retains v3's
`ParsedTxn.related_instrument` and `corporate_action_ratio` support for an
explicitly printed corporate-action replacement, and changes evidence identity
so replaceable geometry cannot alter semantic rows. Resolver-only listing
metadata (issuer/security keys, journalability, and provider symbol) is carried
on `ParsedInstrument` after parsing; institution parsers remain network-free.
Version 6 adds statement page ownership and structured scope-blocking issues.

## Required semantics

- Parsing is deterministic and side-effect free: no database writes or network.
- Every statement has a defensible account and ISO period.
- Every monetary/position row carries native currency.
- Every recognized row preserves the printed description/raw evidence.
- A position-affecting transaction has an instrument or is quarantined. The
  staged resolver may temporarily clear an uncertain printed name only when it
  marks the row `unresolved_printed_identity`; the later holdings-name
  reconciliation must resolve it uniquely or leave it visibly unresolved.
- An option retains root, expiry, strike, call/put, currency, and multiplier.
- Missing or invalid numeric text is `None`/quarantine, never zero by fallback.
- Transaction signs represent the printed/economic event consistently; no
  consumer should need institution-specific sign guesses.
- Output statement identities are unique within a source.
- Every persisted statement owns an ordered, in-range set of physical PDF
  pages. Multi-statement sources require parser-explicit membership.
- A parser declares the scope and completeness of every holdings/cash section.
- Every `partial`/`unknown` scope includes a blocking `ParsedScopeIssue`; a
  `complete` scope cannot include one.
- A parser preserves its printed instrument identity; it does not need to guess
  a public ticker from an uncertain free-form name.
- A compact company/fund name is not a ticker merely because it contains only
  ticker-legal characters. Parsers mark description-derived tokens unresolved;
  the staged resolver owns listing identity.
- A ticker/name-change row may link old and new instruments only when both
  symbols are printed explicitly. The pair must differ and retain one asset
  type/native currency; the conversion ratio must be positive.

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

For `name_change`, the shared enrichment accepts explicit phrases such as
`SYMBOL CHANGE FROM FB TO META`. It rejects company-name-only text and transfer
direction words. A generic name change with no explicit pair remains an
underdetermined movement and makes reconciliation incomplete.

## Evidence and quarantine

`SourceSpan` can carry raw text, page/line, bounding box, words, and parser
rule. Transactions, positions, cash balances, snapshot sets, and the richer
quarantine type can carry one. `ParsedScopeIssue` carries a stable code,
severity, structured detail, optional evidence, and an optional reference to
the quarantine item that caused it. Normal ingest uses raw text plus deterministic
page/line hints. RBC additionally receives transient page words to interpret
its financial debit/credit columns; persisted Verify geometry remains owned by
the independent enrichment pass. The writer assigns every
parsed/quarantined row a deterministic evidence record from source identity,
row kind/occurrence, raw text, and parser rule. Page, line, boxes, and words do
not participate in the `ev2` key. Cash balances carry their opening/closing
source line(s).

The validator reports missing cash evidence and undeclared row scopes as
explicit warnings. It treats malformed dates/currencies/numerics, invalid
transaction vocabulary, incomplete options, duplicate statement identities,
invalid snapshot declarations, missing/invalid page ownership, source spans
outside their statement pages, contradictory scope issues, and parser-reported
errors as fatal.

`PdfText` retains raw page text and page dimensions. It carries
`PdfWord`/`PdfLine` layout rows when extraction explicitly sets
`include_layout=True`; normal ingest does this only for RBC semantic column
signs, while the independent geometry pass does it for every supported PDF.
The parser bridge keeps original raw evidence. Text fixtures and the `pypdf`
fallback receive deterministic page/line evidence but no invented box or word
coordinates. See [INGESTION.md](INGESTION.md) for the replaceable enrichment
pass.

The four bank parsers preserve page membership while splitting page-indexed
text and constrain fallback source lookup to those pages. They attach spans
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

1. a complete option contract is retained before any underlying-listing lookup;
2. an explicit printed symbol is retained or enriched through the listing catalog;
3. an exact reviewed alias or resolved reviewed fund lookup is applied;
4. one exact same-statement holding identity is applied; or
5. the printed identity remains unresolved with confidence `0.0`.

The resolver must never replace a complete option with its root equity. Root,
expiry, strike, call/put, multiplier, and native currency together are the
contract identity even when the root is also a catalogued public ticker.

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
- The bank parsers use broker-specific text state machines rather than a
  general table engine. Geometry enrichment is for visual verification, not a
  second authority for financial values. Debit/credit coverage must be
  spot-checked for each new semantic layout.
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

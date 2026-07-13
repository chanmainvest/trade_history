# Current state

Audit date: **2026-07-12**. The live counts below are a dated diagnostic
snapshot, not a release promise. Re-run the audit before relying on them.

## Product surface

- The FastAPI backend and React GUI build and run.
- The GUI has Transactions, Monthly, Performance, Research, Visualisations,
  Verify extraction, and Settings tabs.
- Statement/PDF review endpoints are read-only. Preferences are written to
  `data/config.json`; ledger mutation is CLI-only.
- The intended data model is native-currency ledger data in SQLite plus public
  market/fundamental data in DuckDB.

## Validation baseline

- `uv run python -m pytest -q`: 30 tests passed and seven later-phase
  acceptance requirements are recorded as strict xfails.
- `uv run ruff check src tests`: passed.
- `npm run build` in `frontend/`: passed with Vite's large-bundle warning.
- Parser tests use ten committed synthetic fixtures and no ignored private
  text dumps.

## Measured live ledger

| Item | Count |
|---|---:|
| source files | 338 |
| statements | 444 |
| accounts | 20 |
| instruments | 33,018 |
| transactions | 2,988 |
| position snapshots | 6,203 |
| cash balances | 459 |
| initial positions | 3,339 |
| initial cash rows | 13 |
| quarantine rows | 5,527 |
| annual performance rows | 5 |
| account links | 7 |
| position/transaction links | 483 |

## Confirmed correctness defects

1. **Instrument identity is not unique in practice.** The unique constraint
   contains nullable option columns, and SQLite treats `NULL` values as
   distinct. The audit found 803 duplicate logical groups, 31,567 excess IDs,
   and 28,587 unreferenced instrument rows.
2. **RBC CAD and USD blocks overwrite each other.** Both parser outputs use the
   same statement key. Writing the later block deletes the earlier children.
3. **TD 2018–2022 bundled months are not split correctly.** Repeated statement
   keys overwrite segments; 470 audited TD transactions fall outside the
   stored statement period.
4. **Source replacement is not atomic.** Statements are activated one at a
   time, duplicate output keys are not rejected, obsolete prior outputs are
   not removed, and a failed attempt can leave stale active children.
5. **“Reconciliation” is link attribution only.** No expected close, actual
   close, residual, tolerance, or status is computed or persisted.
6. **Cash does not reliably roll forward.** Using
   `closing = opening + SUM(net_amount)`, 198 of 459 live rows differ by more
   than one cent; the text-corpus check fails 316 statement/currency cases.
7. **Positions do not reliably roll forward.** Grouped by logical instrument,
   492 of 5,517 consecutive snapshot intervals have a quantity residual.
8. **Monthly joins checkpoints and movements by unstable `instrument_id`.** A
   logical security can appear as duplicate rows, and post-checkpoint rows can
   be unpriced. Monthly diff keys also omit currency/canonical identity.
9. **Snapshot completeness is not represented.** Monthly and Performance can
   clear prior positions merely because some rows exist on a later date.
10. **Unknown numbers can become zero.** Parsers contain `parsed or 0.0`
    fallbacks for quantities and cash, making parse failure indistinguishable
    from a reported zero.
11. **Provenance is too weak.** Cash has no raw line, parsed rows lack page/word
    coordinates, and quarantine identity omits occurrence. The live DB has
    4,620 exact duplicate quarantine rows under a coarse logical key.

## Corpus evidence

Phase 1 added `ledger audit extraction`, which parses without opening SQLite
and writes deterministic JSONL without raw statement text.

- The 324 stored text dumps emitted 593 statements: 171 duplicate statement
  keys, 263 unbalanced calculable cash checks, 205 incomplete cash checks, and
  519 unbalanced position intervals out of 5,585.
- All 338 source PDFs completed in about five minutes and emitted 617
  statements: 178 duplicate statement keys, 270 unbalanced calculable cash
  checks, 214 incomplete cash checks, and 533 unbalanced position intervals out
  of 5,941.
- No source was unclaimed and no parser crashed in either run.

The larger emitted counts are pre-persistence and expose segments that the
current writer would overwrite. They are a defect baseline, not validated
ledger rows.

## Operational/documentation debt

- `quarantine.jsonl` and skipped-PDF logs are append-only, not mirrors of the
  active database.
- The current ingestion cache uses only source path/hash/status, not parser,
  resolver, or schema version.
- New fatal parser-contract errors are blocked before child-row writes, but the
  current source row still lacks a separate last-good active run.
- The target repair and cutover are defined in
  `plan/EXTRACTION_RECONCILIATION_REFACTOR.md`; they are not implemented yet.

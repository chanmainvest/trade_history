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

- `uv run python -m pytest -q`: 38 tests passed and five later-phase
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

1. **The dated live ledger still has broken instrument identity.** Schema v6
   gives new/migrated rows one non-null canonical key and collapses duplicate
   references, but the 2026-07-12 live snapshot has not been migrated or
   shadow-rebuilt. That snapshot contains 803 duplicate logical groups, 31,567
   excess IDs, and 28,587 unreferenced instrument rows.
2. **RBC CAD and USD blocks overwrite each other.** Both parser outputs use the
   same statement key. Writing the later block deletes the earlier children.
3. **TD 2018–2022 bundled months are not split correctly.** Repeated statement
   keys overwrite segments; 470 audited TD transactions fall outside the
   stored statement period.
4. **Source replacement is not atomic.** Duplicate parser output keys are now
   rejected before writes and failed attempts retain the active-run pointer,
   but statements are still activated one at a time and obsolete prior output
   is not removed.
5. **“Reconciliation” is still link attribution only.** Schema v6 can persist
   expected/actual close, residual, tolerance, and status, but no engine
   computes or stores those results yet.
6. **Cash does not reliably roll forward.** Using
   `closing = opening + SUM(net_amount)`, 198 of 459 live rows differ by more
   than one cent; the text-corpus check fails 316 statement/currency cases.
7. **Positions do not reliably roll forward.** Grouped by logical instrument,
   492 of 5,517 consecutive snapshot intervals have a quantity residual.
8. **Holdings consumers are only partly aligned.** Monthly now keys movements
   and diff rows by canonical identity/currency; Performance respects complete
   scoped checkpoints. Visualisations and the broader shared-holdings refactor
   remain pending, and post-checkpoint rows can still be unpriced.
9. **Parser completeness is still unproven.** Schema v6 represents scoped
   completeness and Monthly/Performance refuse to clear from partial/unknown
   scopes, but current parser outputs are stored as `unknown` until Phase 4
   can prove their sections complete.
10. **Unknown numbers can become zero.** Parsers contain `parsed or 0.0`
    fallbacks for quantities and cash, making parse failure indistinguishable
    from a reported zero.
11. **Legacy/live provenance remains weak.** New rows carry deterministic
    evidence records and cash raw lines; parser v1 still lacks page/word
    coordinates and the live snapshot retains 4,620 exact duplicate quarantine
    rows under its old coarse identity.

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
- `ingestion_runs` and active-run pointers exist, but source-wide staged
  activation, content hashes, and truthful cache invalidation remain pending.
- The target repair and cutover are defined in
  `plan/EXTRACTION_RECONCILIATION_REFACTOR.md`; they are not implemented yet.

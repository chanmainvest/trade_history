# Current state

Implementation review: **2026-07-14**. The live-ledger counts below remain a
dated diagnostic snapshot from **2026-07-12**, not a release promise. Parser
v2 was validated by fresh read-only corpus audits on 2026-07-14; the live
SQLite ledger has not been re-ingested or shadow-rebuilt with that output.

## Product surface

- The FastAPI backend and React GUI build and run.
- The GUI has Transactions, Monthly, Performance, Research, Visualisations,
  Verify extraction, and Settings tabs.
- Statement/PDF review endpoints are read-only. Preferences are written to
  `data/config.json`; ledger mutation is CLI-only.
- Ingestion stages one validated PDF source in a savepoint and activates it
  atomically. A failed parse, validation, staged write, or explicit skip keeps
  the prior active extraction.
- CIBC, HSBC, RBC, and TD parsers report version `2.0.0`. They retain source
  page/line evidence, available word/box geometry, explicit snapshot scopes,
  and quarantine rather than fabricate unsupported values.

## Validation and corpus audits

- The committed synthetic-fixture suite covers 11 files. Its extraction audit
  emits 17 statements with zero duplicate keys, zero contract errors, zero
  calculable cash residuals, and zero calculable position residuals. One annual
  RBC fixture is intentionally unclaimed because the generic fixture folder is
  not a production broker folder.
- The 324 stored text dumps produced 323 valid parses and one explicit tax
  document skip; there were zero invalid, unclaimed, or failed files, zero
  contract errors/warnings, and zero duplicate statement keys. The audit still
  reports 287 unbalanced calculable cash checks, 196 incomplete cash checks,
  and 574 unbalanced position intervals: these are unresolved quality findings,
  not hidden adjustments.
- All 338 source PDFs produced 337 valid parses and one explicit tax document
  skip; there were zero invalid, unclaimed, or failed files, zero contract
  errors/warnings, and zero duplicate statement keys. It reports 298 unbalanced
  calculable cash checks, 205 incomplete cash checks, and 588 unbalanced
  position intervals. Phase 5 must persist and explain those residuals.
- The normal structural checks are run before a phase is completed: Python
  tests, Ruff, the frontend production build, and generated-docs build/check.

## Measured live ledger (2026-07-12)

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

## Implemented parser repairs; live rebuild still pending

1. **RBC CAD/USD retention is fixed for parser v2.** One monthly physical
   account/period now contains native CAD and USD child scopes; the committed
   fixture and both full audits emit no duplicate statement identities. Existing
   active rows were produced earlier and remain unchanged until re-ingest.
2. **TD bundled-period splitting is fixed for parser v2.** Full and legacy
   period headers split before account scopes, and repeated account fragments
   merge into one logical statement. The full audits report no duplicate keys
   or contract date violations; rows outside a statement period are quarantined
   until a pending-transaction model exists.
3. **No parser v2 numeric fallback turns a failed parse into zero.** Invalid
   quantities, closing balances, incomplete option contracts, and unmodelled
   activity rows are evidence-linked quarantine items. Historical live rows may
   still contain the old parser output.
4. **New parser output has scoped evidence.** Recognized holdings sections and
   cash sections with a valid printed close can be `complete`; unrecognized or
   incomplete sections remain `unknown`. Text-only extraction supplies stable
   page/line evidence, and `pdfplumber` extraction supplies available
   coordinates/words.

## Confirmed remaining correctness work

1. **The dated live ledger still has broken historical instrument identity.**
   Schema v6 gives new/migrated rows one canonical key, but the 2026-07-12 live
   snapshot has not been shadow-rebuilt. It contains 803 duplicate logical
   groups, 31,567 excess IDs, and 28,587 unreferenced instrument rows.
2. **Reconciliation is still link attribution, not persisted reconciliation.**
   The schema can store expected/actual close, residual, tolerance, and status,
   but no engine computes or stores those results yet.
3. **Full-corpus cash and position residuals remain material.** The current
   parser audit exposes them without fabricating balancing rows; Phase 5 must
   classify and persist them, and a later review must spot-check the sources.
4. **Holdings consumers are only partly aligned.** Monthly uses canonical
   identity and complete scopes, but Performance and Visualisations still have
   separate state engines; post-checkpoint rows can remain unpriced.
5. **Live scopes/provenance are historical.** Parser v2 can produce complete
   scope and coordinate-aware evidence, but currently active v1-derived rows
   remain conservative/legacy until an approved re-ingest and shadow rebuild.

## Operational and documentation limits

- `ingestion_attempts.jsonl`, `quarantine.jsonl`, and `skipped_pdfs.log` are
  regenerated indexes without raw statement text. Historical local log files
  may still need cleanup.
- Cache validity includes source hash, parser version, parser contract, schema,
  and reviewed-identity resolver state. The v2 parser bump makes v1 active
  output stale for a reviewed re-ingest.
- The target reconciliation, shared holdings service, shadow rebuild, and
  cutover remain defined in
  `plan/EXTRACTION_RECONCILIATION_REFACTOR.md`; they are not implemented yet.

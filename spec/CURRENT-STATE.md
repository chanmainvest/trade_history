# Current state

Implementation review: **2026-07-17**. The live-ledger counts below remain a
dated diagnostic snapshot from **2026-07-12**, not a release promise. Parser
v2 and the reconciliation engine were validated in fixtures and read-only
corpus audits on 2026-07-14. A new parser-v2 shadow ledger has been built and
compared, but the live SQLite ledger has not been re-ingested, changed, or cut
over to it.

## Product surface

- The FastAPI backend and React GUI build and run.
- The GUI has Transactions, Monthly, Performance, Research, Visualisations,
  Verify extraction, and Settings tabs.
- Statement/PDF review endpoints are read-only. Preferences are written to
  `data/config.json`; ledger mutation is CLI-only.
- Ingestion stages one validated PDF source in a savepoint and activates it
  atomically. A failed parse, validation, staged write, or explicit skip keeps
  the prior active extraction.
- CIBC and RBC report parser version `2.2.0`, HSBC reports `2.1.0`, and TD
  reports `2.1.0`. They retain source page/line evidence, available word/box
  geometry, explicit snapshot scopes, and quarantine rather than fabricate
  unsupported values.
- Monthly, Performance, and Visualisations now consume one read-only scoped
  holdings service. Monthly renders checkpoint/provenance, reported versus
  reconstructed/incomplete state, reconciliation, and stale/unpriced quality
  fields without mutating the ledger.
- Verify extraction now reads active parser/run metadata, scope completeness,
  position/cash/statement-total reconciliation results, and source-linked cash
  and summary-total rows. Its unresolved/incomplete/unreconciled filters are
  read-only. Legacy rows without v6 facts are shown as unavailable, not complete.
- Transactions exposes initial-position anchors separately from broker events.
  Transactions and Monthly can deep-link exact source rows into Verify; the
  preference is controlled in Settings. Monthly no longer repeats a portfolio
  column, and its tickers link to Research.
- The CLI now has a guarded shadow build/compare/sign-off/cutover/rollback
  workflow. Building a shadow never changes the live ledger; cutover requires
  a signed local review report, a stopped backend acknowledgement, and an exact
  live-filename confirmation.

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
  errors/warnings, and zero duplicate statement keys. The read-only parser
  audit reports 298 unbalanced calculable cash checks, 205 incomplete cash
  checks, and 588 unbalanced position intervals. The reconciliation engine now
  persists equivalent scoped outcomes when the ledger is ingested or rebuilt;
  these audit counts are not a rerun against the dated live database.
- Reconciliation regressions cover persisted position components and residuals,
  cash settlement dates across statement ownership, adjacent cash continuity,
  position and portfolio totals, incomplete scopes, idempotent rebuilding, and
  a TD bundled-period golden fixture with no unexplained cash or position
  result.
- The normal structural checks are run before a phase is completed: Python
  tests, Ruff, the frontend production build, and generated-docs build/check.
- On 2026-07-15, `ledger shadow build` parsed all 338 source PDFs twice into
  `data/ledger.vnext.sqlite`; both target content fingerprints matched and the
  before/after PDF manifest matched. The shadow contains 548 statements, 757
  canonical instruments, 3,382 transactions, 7,955 position snapshots, 625
  cash balances, and 9,789 reconciliation results. Its redacted comparison
  report is pending human source spot-check/sign-off; no cutover occurred.
- On 2026-07-17, a disposable post-repair shadow parsed the same 338 PDFs with
  CIBC/RBC `2.2.0`, HSBC `2.1.0`, and TD `2.1.0`. The PDF manifest was identical
  before/after. It contains 548 statements, 657 instruments, 3,387
  transactions, 6,665 position snapshots, 625 cash balances, 120 initial
  positions, and 8,485 reconciliation results. Its referenced-symbol audit has
  286 instrument/currency identities, zero reserved/invalid symbols, zero
  `TO`/`FROM` instruments, zero negative reported non-option positions, and
  zero reversed-sign equity buys/sells. This disposable build did not request
  the two-build reproducibility check and was not signed off or cut over.

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

## Implemented parser repairs; shadow review pending

1. **RBC CAD/USD retention is fixed for parser v2.** One monthly physical
   account/period now contains native CAD and USD child scopes; the committed
   fixture and both full audits emit no duplicate statement identities. Existing
   active rows were produced earlier and remain unchanged. The new shadow has
   92 monthly RBC statements and 3 annual RBC reports; the legacy database had
   91 and 6 respectively, so the annual-report difference remains a manual
   review item rather than an automatic cutover decision.
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
5. **Unresolved printed names no longer become tickers.** CIBC account-transfer
   direction tokens (`TO`/`FROM`) have no instrument, name-only identities must
   resolve through reviewed/exact evidence before persistence, and unresolved
   positions are quarantined with a non-clearing scope. HSBC column headers can
   no longer become `CAD`/`USD` holdings.
6. **Derived same-period statements are canonicalized.** Duplicate/reissued
   PDFs remain verifiable, while initials, holdings, transaction lists, transfer
   pairing, and reconciliation use the latest persisted revision once.
7. **Holdings and performance have bounded state.** Complete checkpoint
   omission cannot revive an obsolete initial position, and Performance stops
   carrying an account after 90 days without a checkpoint. CAD/USD remain
   separate native series.

## Confirmed remaining correctness work

1. **The dated live ledger still has broken historical instrument identity.**
   Schema v6 gives new/migrated rows one canonical key, but the 2026-07-12 live
   snapshot is not the shadow target. It contains 803 duplicate logical
   groups, 31,567 excess IDs, and 28,587 unreferenced instrument rows.
2. **The reconciliation engine is implemented, but the dated live ledger has
   not been rebuilt with it.** `ledger ingest reconcile` now stores
   source-traceable position, cash, and printed-total equations. This phase did
   not mutate the live database. The GUI can surface results when they exist,
   but legacy live rows have no v6 reconciliation facts yet.
3. **Full-corpus cash and position residuals remain material.** The parser
   audit and shadow reconciliation expose them without fabricating balancing
   rows. The largest residuals and the RBC annual-report count difference still
   need source spot-checks before a human can sign off or cut over.
4. **The holdings engine is shared, but live data quality is still historical.**
   Monthly, Performance, and Visualisations now use canonical identity,
   complete scopes, normalized movements, and explicit stale/unpriced status.
   The dated live ledger has not been re-ingested or reconciled with parser v2,
   so visible quality states remain historical/unavailable until review and
   cutover.
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
- The GUI quality surface is implemented read-only. Shadow build/cutover
  tooling is implemented, but review sign-off and cutover have intentionally
  not occurred.

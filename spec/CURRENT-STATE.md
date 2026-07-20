# Current state

Implementation review: **2026-07-19**. Parser v2 and the reconciliation engine
were validated in fixtures, full-corpus shadow builds, and a reproducible
two-build fingerprint comparison. The signed shadow was cut over to the real
`data/ledger.sqlite` profile on 2026-07-19; the pre-cutover database remains as
a timestamped local backup.

## Product surface

- The FastAPI backend and React GUI build and run.
- The GUI has Transactions, Monthly, Performance, Research, Visualisations,
  Verify extraction, and Settings tabs.
- Statement/PDF review endpoints are read-only. Preferences are written to
  `data/config.json`; ledger mutation is CLI-only.
- Ingestion stages one validated PDF source in a savepoint and activates it
  atomically. A failed parse, validation, staged write, or explicit skip keeps
  the prior active extraction.
- The current worktree/shadow uses CIBC/RBC/TD parser version `2.6.0` and HSBC
  `2.5.0`.
  They retain semantic source page/line evidence, explicit snapshot scopes,
  and quarantine rather than fabricate unsupported values. Word/box geometry
  is rebuilt separately after semantic activation.
- Monthly, Performance, and Visualisations now consume one read-only scoped
  holdings service. Monthly renders checkpoint/provenance, reported versus
  reconstructed/incomplete state, reconciliation, and stale/unpriced quality
  fields without mutating the ledger.
- Verify extraction now renders statement-owned physical pages, uses
  evidence-specific rectangles and bidirectional pane-local selection, places
  financial rows first, and shows structured scope issues/reconciliation at
  the bottom. Its unresolved/incomplete/unreconciled filters are read-only.
- Schema v10 constrains new ledger currencies to CAD/USD, validates canonical
  business dates/UTC timestamps and SHA-256 text, and stores replaceable PDF
  geometry separately from `ev2` semantic evidence. It adds explicit statement
  pages, scope blockers, and structured reconciliation reasons. Verify reads
  persisted exact links; ambiguous/unmatched rows remain visibly unlinked.
- Transactions exposes initial-position anchors separately from broker events.
  Transactions and Monthly can deep-link exact source rows into Verify; the
  preference is controlled in Settings. Monthly no longer repeats a portfolio
  column, and its tickers link to Research.
- The CLI now has a guarded shadow build/compare/sign-off/cutover/rollback
  workflow. Building a shadow never changes the live ledger; cutover requires
  a signed local review report, a stopped backend acknowledgement, and an exact
  live-filename confirmation.

## Validation and corpus audits

- The committed synthetic-fixture suite covers 14 files. Its extraction audit
  emits 18 statements with zero duplicate keys, zero contract errors, zero
  calculable cash residuals, and zero calculable position residuals. One annual
  RBC fixture and two parser-only HSBC edge fixtures are intentionally
  unclaimed because their generic fixture paths/headers are not production
  broker recognition inputs.
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
- On 2026-07-17, the latest disposable post-repair shadow parsed the same 338 PDFs with
  CIBC/RBC/TD `2.2.0` and HSBC `2.1.0`. The PDF manifest was identical
  before/after. It contains 548 statements, 658 instruments, 3,519
  transactions, 6,669 position snapshots, 625 cash balances, 105 initial
  positions, and 8,490 reconciliation results. Its referenced-symbol audit has
  286 instrument/currency identities, zero reserved/invalid symbols, zero
  `TO`/`FROM` instruments, zero negative reported non-option positions, and
  zero reversed-sign equity buys/sells. This disposable build did not request
  the two-build reproducibility check and was not signed off or cut over.
  TD `2.2.0` retains 132 additional valid transactions, eliminates all 141
  instances of the former name-only buy/sell identity quarantine reason, and
  resolves the May 27 VELO buy to its exact same-statement holding. The false
  May 30 VELO inferred initial is absent.
- On 2026-07-18, a fresh disposable shadow parsed all 338 PDFs with CIBC/RBC/TD
  `2.3.0`; the before/after PDF manifest remained identical. It contains 548
  statements, 657 instruments, 3,519 transactions, 6,669 position snapshots,
  625 cash balances, 103 initial positions, and 8,490 reconciliation results.
  A new rebuildable holding-name reconciliation pass resolved 79 of the 206
  formerly null-instrument buy/sell rows: 75 from unique same-account/native-
  currency checkpoint evidence and four from strict portfolio-wide evidence.
  Remaining unresolved buy/sells are 127 (CIBC ID 70, CIBC TFSA 4, HSBC 1,
  RBC 30, TD 22); generic or ambiguous names remain unresolved. Invalid/
  reserved referenced symbols, negative reported non-option positions, and
  reversed-sign equity buys/sells remain zero. USD Barrick name fallback is
  now `GOLD`, while CAD remains `ABX`. Position results moved from 4,591 to
  4,094 incomplete inputs and from 2,477 to 2,892 reconciled results; 82 newly
  calculable intervals expose real residuals instead of being hidden as
  incomplete. This shadow was not signed off or cut over.

- On 2026-07-19, a fresh disposable shadow parsed the unchanged 338-PDF
  manifest with CIBC `2.5.0`, RBC/TD `2.5.1`, HSBC `2.4.0`, and resolver v5. It contains
  548 statements, 644 instruments, 4,236 transactions, 6,737 position
  snapshots, 648 cash balances, 45 initial positions, and 8,824 reconciliation
  results. Cash results are 827 reconciled, 42 unexplained residuals, 402
  incomplete inputs, and 17 missing-prior checks. Position results are 1,033
  reconciled, 28 unexplained residuals, 6,439 incomplete inputs, 34
  missing-prior checks, and two not-applicable scopes. The remaining position
  residuals are RBC (24) and HSBC (4); TD has none because degraded legacy
  holding tables now remain explicitly incomplete rather than masquerading as
  complete checkpoints. A source audit found zero
  null/contrary quantity signs
  across 360 buys and 223 sells after complete option contracts were prevented
  from collapsing into their root equities. RBC `2.5.1` recovers compact
  historical activity dates and balances and reduced RBC position residuals
  from 42 to 24. TD `2.5.1` retains signed holding quantities: seven negative
  non-option snapshots are source-backed SMCI/RDDT shorts, while NTR has no
  negative equity snapshot. Unrecognized numeric TD holding rows downgrade the
  whole positions scope while retaining readable rows. Two clean rebuilds
  produced the same content fingerprint, the user signed off the report, and
  this database was atomically promoted to the real profile. The prior live
  database is retained as `ledger.backup-20260719T215224Z.sqlite`. A subsequent
  layout-enrichment pass persisted 2,896 pages, 141,250 lines, and 21,808 exact
  evidence links; 2,329 ambiguous and 1,481 unmatched links remain explicit.
  Remaining RBC/HSBC residuals still require source review.

- On 2026-07-19, the schema-v10/parser-contract-v6 worktree produced two clean
  shadow builds with the same content fingerprint and the unchanged 338-file
  coverage: 548 statements and 4,236 transactions, with zero RBC/TD statement
  deltas from the current real ledger. A disposable geometry pass covered 337
  broker sources (the remaining source is an explicit tax-document skip),
  2,896 pages, and 141,250 lines. It produced 24,374 exact, 1,349 ambiguous,
  and 1,156 unmatched evidence matches. Hard audits found zero statements
  without pages, zero incomplete scopes without blockers, zero complete scopes
  with blockers, zero linkable evidence without rectangles, and zero displayed
  evidence outside statement-owned pages. This new shadow remains unsigned and
  was not cut over; the real profile still uses the prior signed July 19
  database until human UI/PDF review approves a separate cutover.

## Listing/provider identity support (fixture validated 2026-07-18)

- Schema v9 separates issuer, security/share class, broker listing, Yahoo
  provider symbol, unresolved candidate, and explicit journal-pair identities.
- Resolver v5 no longer accepts compact HSBC company/ETF names as tickers and
  retains a complete option contract before looking up its root equity.
  Reviewed catalog mappings cover the observed BCE, BMO money-market, Global X
  cash/currency/T-bill, iShares LQD, Nutrien, Rogers Class B, Purpose PSA,
  US Benchmark Treasury, Sprott, TC Energy, TELUS, SPDR BILS, and Vanguard bond
  variants. Unknown names remain queued and absent from financial-row identity.
- On 2026-07-18 the live-profile database was backed up, compatibility-migrated
  from schema v5 to v9, and passed integrity/foreign-key checks. The catalog
  repair repointed 161 transactions, 128 non-conflicting snapshots, and 47
  initial rows; 41 checkpoint collisions remain for clean shadow re-ingest
  rather than merging source facts. The complete observed HSBC pseudo-ticker
  list and bare `RCI` have zero references from transactions, snapshots, or
  initials; Rogers references use `RCI.B`. This was not a full shadow rebuild
  or cutover.
- Yahoo verification is opt-in and requires a unique strong public-name match
  in the expected currency/listing family plus non-empty history. Network
  approval infrastructure was unavailable during this implementation, so
  live Yahoo verification was not claimed; mocked verification and provider-
  symbol selection regressions pass.

## Ticker-change support (fixture validated 2026-07-18)

- Schema v9 preserves old and new tickers as separate canonical instruments
  linked by an effective date and source transaction/evidence. It does not use
  the timeless alias table for this purpose.
- Parser-contract v5 accepts only explicit printed old/new pairs. Generic name
  changes remain incomplete rather than being inferred from names or residuals.
- Reconciliation debits the whole old-symbol balance and credits the new-symbol
  balance at the stored ratio. Holdings, Monthly diff, Performance filters, and
  Research consume the same non-branching lineage; Research constrains each
  ticker's public data to its validity dates.
- Synthetic activation, reconciliation, holdings, and ambiguity regressions
  pass. The 2026-07-19 shadow exercised this code across the full corpus and
  observed no ticker-change source rows; absence of an observed relationship
  does not authorize an inferred one. The promoted live ledger therefore has
  no source-backed ticker-change relationship yet.

## Legacy pre-cutover live-ledger baseline (2026-07-12)

These counts describe the replaced database and are retained only as a
historical comparison with the rebuilt live counts above.

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

1. **The live ledger uses the prior rebuilt identity model.** Schema v9 and
   resolver v5 keep listing, security, issuer, provider symbol, ticker change,
   and journal-pair identities separate. Remaining unresolved candidates are
   review data, not invented financial-row tickers.
2. **The live ledger now contains persisted reconciliation facts.** Position,
   cash, and printed-total equations are source traceable and available to the
   GUI. Incomplete inputs remain visibly incomplete.
3. **Full-corpus cash and position residuals remain material.** The parser
   audit and shadow reconciliation expose them without fabricating balancing
   rows. The largest residuals and the RBC annual-report count difference still
   need source spot-checks before those intervals can be considered reconciled.
4. **The holdings engine is shared and reads the rebuilt live data.**
   Monthly, Performance, and Visualisations now use canonical identity,
   complete scopes, normalized movements, and explicit stale/unpriced status.
5. **Schema-v10 Verify provenance is validated in a non-live shadow.** Exact
   links drive highlighting; ambiguous/unmatched links remain disabled review
   states and are never resolved by guessing. Human PDF/UI review and guarded
   cutover are still required before the live profile receives these changes.

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

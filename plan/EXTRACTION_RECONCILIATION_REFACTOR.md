# Extraction, Reconciliation, and Month-End Refactor Plan

Status: in progress — Phases 0–4 completed and validated 2026-07-14
Baseline audit date: 2026-07-12
Scope: documentation truth reset, statement extraction, ingestion, security/cash reconciliation, and the Monthly/Performance read models

**First action:** complete Phase 0—the `AGENTS.md`/`spec/` truth reset and on-demand context split—before changing the schema, parsers, reconciliation, or read models.

## 1. Outcome

Refactor the ledger so that:

- every persisted value is traceable to a PDF location;
- a parser cannot silently overwrite another statement segment;
- an instrument has one stable identity across transactions and snapshots;
- complete statement snapshots remain the ground-truth checkpoints;
- transaction roll-forward is explicit, testable, and reconciled to the next checkpoint;
- the Monthly, Performance, and visualization APIs consume one holdings engine;
- unexplained differences are reported, not hidden with guessed rows or adjustments; and
- `AGENTS.md`, `spec/`, `README.md`, generated docs, tests, and the live code describe the same system.

This is a correctness rebuild of the data path. It is not a GUI redesign, an OCR project, or a request to move/rename the source PDFs.

## 2. Audit baseline

The plan is based on the current dirty working tree. Existing user changes—especially the removal of statement upload/LLM mutation endpoints and the addition of the read-only Verify tab—must be preserved and treated as the current intended product direction.

### 2.1 Repository and validation baseline

- 108 tracked files and 100 non-ignored files were inventoried.
- The current local suite passes: 27 tests.
- `ruff` passes for `src` and `tests`.
- The frontend production build passes; Vite reports a large-bundle warning.
- Parser tests read `data/text_dumps/`, which is git-ignored. They pass on this machine but are not self-contained in a clean checkout/CI environment.
- `docs/index.html` is stale relative to the current `spec/` files and still advertises removed upload/LLM behavior.
- Several one-off scripts query columns that do not exist (`accounts.name`, `source_files.parse_error`) or contain an unrelated absolute path.

### 2.2 Live database baseline

The current `data/ledger.sqlite` contains:

| Item | Current count |
|---|---:|
| Source files | 338 |
| Statements | 444 |
| Transactions | 2,988 |
| Position snapshots | 6,203 |
| Cash balances | 459 |
| Quarantine rows | 5,527 |
| Instruments | 33,018 |
| Initial positions | 3,339 |
| Position/transaction links | 483 |

These counts already disagree with the static “current state” block in `AGENTS.md`.

### 2.3 Confirmed correctness defects

1. **Instrument identity is broken.** The `instruments` uniqueness constraint includes nullable option columns. SQLite treats `NULL` values as distinct, so the ordinary-equity/fund upsert does not conflict. The live database has 803 duplicate logical instrument groups, 31,567 excess instrument rows, and 28,587 unreferenced instrument rows. One logical fund has 724 IDs.

2. **RBC currency blocks overwrite one another.** The parser emits CAD and USD blocks with the same `(source_file, account, period_end)` identity. `_write_statement()` deletes the first block's children when it writes the second. Among 91 RBC monthly statements in the database, none contains both CAD and USD position scopes; recent statements retain only USD.

3. **TD bundled periods overwrite one another.** The parser only splits one legacy header form. Thirty-four audited TD bundles emit repeated statement keys, accounting for 100 overwritten segments. Several 2018–2022 quarterly PDFs are treated as one first-month period even though they contain multiple monthly periods. The text-corpus audit found 470 TD transactions outside their assigned statement period.

4. **Source replacement is not atomic at source-file scope.** The writer replaces one emitted statement at a time. It does not preflight duplicate output keys and does not remove old statements that a newer parser version stops emitting.

5. **Reconciliation is attribution, not reconciliation.** `position_transaction_links` links movement rows to snapshots but never computes expected quantity, actual quantity, residual, tolerance, or status. Only 420 of 6,203 snapshots have at least one link.

6. **The current cash contract does not balance.** Using the intended formula `closing = opening + SUM(net_amount)`, 198 of 459 live statement cash rows differ by more than one cent. The 324-file text-dump audit fails 316 statement/currency checks. Causes include lost debit/credit column meaning, incorrect signs, omitted rows, and continuation/header parsing.

7. **Position roll-forward does not balance reliably.** After grouping by logical instrument identity, 492 of 5,517 consecutive snapshot intervals have an unexplained quantity residual.

8. **The Monthly engine joins on unstable IDs.** A transaction and its matching snapshot can have different `instrument_id` values. The API then emits a second, often unpriced row instead of applying the movement to the checkpoint row. At audited dates the view contains duplicate display keys and post-checkpoint rows with no market value.

9. **The Monthly diff key is incomplete.** Backend and frontend diff maps omit currency and canonical instrument identity. The same displayed symbol in CAD and USD can overwrite another row in the diff map.

10. **Snapshot completeness is assumed, not represented.** Any latest position date for an account clears every older security for that account. There is no stored proof that the parser captured a complete account/currency holdings section.

11. **Unknown numeric data can become fabricated zero.** Multiple parsers use `parsed_value or 0.0` for quantities and closing cash. A parse failure and a real zero are therefore indistinguishable.

12. **Quarantine lacks stable source provenance.** There are 4,620 exact duplicate quarantine rows by `(source, account, raw_line, reason)`. Page, line, bounding box, and occurrence are not part of the identity.

### 2.4 Documentation drift confirmed during the audit

- `AGENTS.md` is about 15 KB while describing itself as intentionally short.
- It lists a nonexistent `src/ledger/analytics/` directory.
- It says upload/draft-parser/Settings reconciliation workflows are done even though the current working tree removes them.
- `ARCHITECTURE.md` describes several DuckDB columns and keys differently from `duckdb_store.py`.
- It claims source re-ingestion replaces all stale derived rows; the writer does not do that.
- It describes generated quarantine logs as a mirror, although the JSONL file is append-only.
- Market-data CLI options and retry behavior in the spec do not match the code.
- `README.md`, `prompts/new-parser.md`, and generated docs disagree about the parser-draft HTTP workflow.
- The documented repository name and ingestion/test counts are stale.

## 3. Non-negotiable design rules

1. PDFs remain read-only. Never move, rename, edit, or delete them.
2. A failed extraction never replaces the last known-good active extraction.
3. No missing/invalid number is converted to zero. It is absent or quarantined with evidence.
4. Native currency is preserved at ingestion.
5. A checkpoint may clear omitted positions only inside a scope proven complete.
6. Every transaction, holding, cash balance, and quarantine item has source provenance.
7. Automatic symbol resolution stores its method/evidence; the printed description remains intact.
8. Reconciliation produces residuals and statuses. It never creates a balancing transaction.
9. Parser output is validated in memory before any active ledger rows change.
10. The repaired database is built beside the live database and cut over only after comparison and review.

## 4. Implementation order

The phases below are intentionally ordered. In particular, Phase 0 must finish before schema or parser work begins.

## Phase 0 — Make `AGENTS.md` and `spec/` truthful and load context on demand

### 0.1 Preserve and describe the current working tree

- Record the current modified files and do not revert or overwrite unrelated user changes.
- Treat the current read-only statement API and Verify tab as implemented behavior.
- Label broken or unverified behavior explicitly; do not document target behavior as if it already exists.
- Add an audit date to volatile “current state” data, or generate it instead of hard-coding it.

### 0.2 Reduce `AGENTS.md` to a small routing document

Target: roughly 60–90 lines and less than 4 KB.

Keep only:

- the PDF safety, no-fabrication, native-currency, and snapshot-ground-truth rules;
- the dirty-worktree preservation rule;
- `uv`/npm commands and required validation;
- documentation update requirements;
- a task-to-spec context routing table; and
- the instruction to load only the relevant spec before editing a subsystem.

Move out of `AGENTS.md`:

- architecture and schema detail;
- parser tutorials and per-bank quirks;
- local server troubleshooting;
- release procedure;
- old “deferred/done” history;
- volatile ingestion counts;
- frontend walkthroughs; and
- long examples.

Proposed routing table:

| Work area | Load before changing |
|---|---|
| Schema/SQLite/DuckDB | `spec/DATA-MODEL.md` |
| PDF extraction/ingestion | `spec/INGESTION.md` |
| Reconciliation/holdings | `spec/RECONCILIATION.md` |
| CIBC/HSBC/RBC/TD parser | `spec/parsers/<institution>.md` plus `spec/PARSER-CONTRACT.md` |
| API/frontend | `spec/API-UI.md` |
| CLI, Docker, release, local servers | `spec/OPERATIONS.md` |
| Current limitations/counts | `spec/CURRENT-STATE.md` |
| Human usage | `spec/USER-GUIDE.md` |

### 0.3 Split the specification into focused context files

Create this on-demand layout:

```text
spec/
  INDEX.md                    short context router and authority rules
  CURRENT-STATE.md            dated, measured state and known broken behavior
  ARCHITECTURE.md             short system map; links to focused specs
  DATA-MODEL.md               canonical SQLite/DuckDB contracts
  INGESTION.md                extraction -> validation -> atomic persistence
  PARSER-CONTRACT.md          parser types, signs, provenance, completeness
  RECONCILIATION.md           formulas, scopes, statuses, holdings-at-date
  API-UI.md                   routes and frontend consumers
  OPERATIONS.md               profiles, CLI, dev servers, release, logging
  USER-GUIDE.md               human-facing behavior only
  EXTRACTION-CORNER-CASES.md  cross-institution lessons only
  parsers/
    CIBC.md
    HSBC.md
    RBC.md
    TD.md
```

`ARCHITECTURE.md` should become a concise map, not another copy of every detailed contract. `INDEX.md` should identify the canonical owner of each fact so the same contract is not maintained in three files.

### 0.4 Reconcile every doc with code before refactoring code

- Correct the current schema/API/CLI/market-data descriptions.
- Mark instrument identity, RBC/TD overwrite, reconciliation, and Monthly reconstruction as known defects.
- Remove stale `analytics/` references.
- Reconcile `prompts/new-parser.md` with the removed HTTP workflow; either make it an explicitly manual/offline prompt or archive it.
- Fix or remove obsolete diagnostic scripts, or document them as unsupported throwaways outside the product surface.
- Update `README.md` to be a short overview and quick start, not another architecture spec.
- Regenerate `docs/index.html` only after the source specs are correct.
- Add a docs check mode to CI so generated docs fail when stale; do not wait for a tagged release to discover drift.

### Phase 0 gate

- An agent can understand its invariant and find the right focused spec by reading only `AGENTS.md`.
- Every current endpoint, table, CLI command, package path, and workflow named in docs exists in code.
- Known broken behavior is labeled broken, not “DONE”.
- `docs/index.html` is reproducible and current.
- Documentation changes preserve the user's existing uncommitted product changes.

## Phase 1 — Build a self-contained regression and corpus-audit harness

### 1.1 Replace private, ignored test dependencies

- Move minimal redacted/synthetic statement text fixtures into `tests/fixtures/`.
- Cover each materially different layout, not just one file per institution:
  - CIBC normal, continued activity, dual currency, options, funds;
  - HSBC main/continued account pages and negative parentheses;
  - RBC CAD+USD, multi-page activity, annual report;
  - TD modern monthly, 2016–2017 legacy bundles, 2018–2022 quarterly bundles, and modern options.
- Keep real PDFs and full text dumps ignored.
- Confirm tests pass in a clean checkout with no `data/text_dumps/` directory.

### 1.2 Add a parser contract validator

Validate every `ParseResult` before persistence:

- unique statement identities within one source file;
- valid period and transaction dates;
- runtime-enforced transaction vocabulary;
- native currency on all monetary/position rows;
- complete option identity when the row is an option;
- no `None -> 0` coercion;
- no position-affecting transaction without an instrument or explicit quarantine;
- source provenance on every emitted/quarantined item;
- declared snapshot completeness and scope; and
- deterministic source-row keys.

Validation errors make the ingestion attempt fail before replacing active rows. Warnings remain visible in the audit report.

### 1.3 Add a read-only corpus audit command

Add a command such as:

```powershell
uv run ledger audit extraction --statements-dir Statements --output logs/extraction_audit.jsonl
```

It must parse without writing SQLite and report, per source/statement/scope:

- parser/version;
- emitted statement keys and collisions;
- period/date violations;
- transaction/position/cash/quarantine counts;
- source-line coverage;
- unresolved/synthetic instrument identities;
- cash and position reconciliation residuals where calculable;
- incomplete sections; and
- fatal errors.

Use this command for full private-corpus validation, while CI runs the committed fixture subset.

### 1.4 Encode the current failures as regression tests

Add tests that fail on the current implementation and pass only after the relevant phase:

- nullable instrument identity creates no duplicates;
- two uses of the same equity resolve to the same ID;
- RBC CAD and USD both survive persistence;
- every month in a TD bundle survives with the correct period;
- duplicate parser statement keys are rejected before writes;
- source reparse removes obsolete derived statements exactly once;
- failed parse preserves the active last-good extraction;
- a missing cash/quantity parse is quarantined, not zero;
- cash and position residuals are stored with status;
- Monthly keys include currency and canonical instrument identity.

### Phase 1 gate

- Tests are self-contained in a clean checkout.
- The 324 stored text dumps and all 338 current PDFs can be audited without modifying the live database.
- The audit report reproduces the known RBC, TD, instrument, cash, and position failures.

## Phase 2 — Repair the canonical data model

Read and update `spec/DATA-MODEL.md` and `schema.sql` together.

### 2.1 Give every instrument a stable, non-null identity

Add a canonical `instrument_key TEXT NOT NULL UNIQUE`, produced by one shared normalizer.

Suggested fallback keys:

- ordinary instrument: `asset_type|normalized_symbol|currency`;
- option: `option|root|currency|expiry|normalized_strike|type|multiplier`.

Use CUSIP/ISIN or a reviewed alias when available, but never invent one. Exchange may refine identity when the statement reliably supplies it. All parser, repair, transfer, reconciliation, and API code must resolve via this key rather than raw `instrument_id` equality or display symbol.

`upsert_instrument()` should explicitly get-or-create by `instrument_key`; it must not depend on nullable SQLite conflict semantics.

### 2.2 Define one unambiguous statement identity

Use one statement per physical broker account and period. Child scopes represent currency/section completeness.

- RBC CAD and USD blocks become child scopes of one account-period statement.
- CIBC already naturally fits one account-period with CAD/USD children.
- HSBC E/F and TD CAD/USD identifiers remain separate accounts when they are real broker subaccounts.
- Parser output containing the same statement identity twice is invalid.

Use a complete uniqueness key such as `(source_file_id, account_id, period_start, period_end, statement_type)` and add a deterministic `statement_key` for validation/logging.

### 2.3 Represent snapshot scope and completeness

Add a table such as `snapshot_sets`/`statement_sections` with:

- statement/account/date;
- currency;
- section type (`positions`, `cash`, `summary`);
- completeness (`complete`, `partial`, `absent`, `unknown`);
- source span;
- optional reported section/portfolio total; and
- parser validation status.

Link position snapshots and cash balances to their set. Omission clears a prior holding only when the relevant set is `complete`.

### 2.4 Store normalized deltas without destroying reported values

For a transaction preserve:

- printed/reported quantity and amount;
- normalized `position_delta`;
- normalized signed `cash_delta` (`net_amount` may be retained as the compatibility name);
- cash effective date (`settle_date` when the broker cash ledger uses settlement, otherwise trade date);
- resolution method/confidence; and
- source span/raw text.

This removes the need for three consumers to reinterpret transaction types and inconsistent quantity signs independently.

### 2.5 Add source provenance

Every parsed/quarantined row needs a deterministic evidence key containing at least:

- source file fingerprint;
- page number;
- line/row occurrence;
- raw text;
- bounding box or word coordinates when available; and
- parser rule/version.

Cash balances currently have no raw line; add it. The Verify UI should use this stored evidence rather than fuzzy text matching whenever coordinates are available.

### 2.6 Separate ingestion attempts from active extracted data

Add `ingestion_runs` (or equivalent) with source hash, parser name/version, schema version, status, errors, started/finished timestamps, and content counts. A source has at most one active successful run; failed attempts remain auditable without making stale children look current.

### 2.7 Replace link-only reconciliation storage

Keep component links if useful, but add explicit reconciliation records:

- kind (`position`, `cash`, `statement_total`, `transfer`);
- account/instrument/currency/scope;
- prior checkpoint;
- opening/previous value;
- summed deltas;
- expected close;
- reported close;
- residual;
- tolerance;
- status; and
- reason for incomplete/unreconciled results.

### Phase 2 gate

- Repeated upserts of an ordinary instrument return one ID.
- Schema migration tests cover a pre-refactor database.
- All foreign keys and uniqueness constraints are valid.
- Snapshot clearing cannot occur without a complete scope.
- Reconciliation can represent a residual without fabricating an adjustment.

## Phase 3 — Refactor ingestion into validate-then-activate

### 3.1 Use a staged source-file pipeline

Refactor into explicit stages:

```text
discover -> extract layout -> select parser -> parse -> validate
         -> resolve identities -> stage -> atomically activate -> reconcile -> audit
```

No stage before “atomically activate” changes active ledger rows.

### 3.2 Replace an entire source atomically

For one PDF:

1. parse and validate all emitted statements/scopes in memory;
2. reject duplicate statement/source-row keys;
3. begin one database transaction/savepoint;
4. write the new run and all children;
5. switch the source's active run;
6. remove the old derived run through foreign-key cascade; and
7. commit.

On failure, rollback and keep the prior active run. This fixes stale statements, per-segment deletes, and partial fan-out overwrite.

### 3.3 Make cache invalidation truthful

The unchanged check must include at least:

- source SHA-256;
- parser name and version;
- parser contract/schema version; and
- relevant resolver version.

A parser/version change should reparse automatically or be clearly surfaced as stale. Do not require a human to know that `--force` is needed after every parser edit.

### 3.4 Make symbol resolution a recorded phase

- Parse the printed identity first.
- Resolve explicit ticker/option fields first, reviewed aliases second, same-statement holdings third.
- Store `resolution_method`, confidence, and evidence.
- Leave unresolved names as unresolved audited instruments, not guessed public tickers.
- Remove broad post-hoc mutation passes once equivalent deterministic resolution exists in the staged pipeline.
- Keep reviewed fund-code lookup data and user aliases during rebuilds.

### 3.5 Make logs derived and idempotent

- Structured audit logs identify ingestion run and source row.
- Regenerate/export quarantine JSONL from the active database or write run-specific files; do not call an append-only file a mirror.
- Ensure scripts either use the logging contract or are removed from supported tooling.

### Phase 3 gate

- Running ingest twice produces identical active row counts and a stable content hash.
- Instrument count does not grow on a forced re-ingest.
- No source can retain an obsolete statement from an older parser output.
- A parser crash or validation failure leaves the previous active extraction untouched.

## Phase 4 — Rebuild parsers around layout-aware, stateful rows

### 4.1 Introduce a shared layout model

Extend PDF extraction to retain page words/lines with coordinates while preserving raw text. Normalize Unicode artifacts (minus, em/en dash, non-breaking spaces, CIBC glyph substitutions) into parser tokens, but never overwrite raw evidence.

Use coordinates or stable column spans to distinguish:

- debit versus credit;
- quantity, price, amount, and running balance;
- security name versus activity verb; and
- table rows versus headers/footers.

The current plain-line regex approach cannot reliably infer signs from RBC's separate debit/credit columns.

### 4.2 Use state machines, not independent line guesses

Each parser should explicitly track:

- document period;
- account and currency scope;
- current table/section;
- current logical row and continuation lines;
- repeated page headers/footers;
- section completeness; and
- source spans.

A data-looking row not claimed by a rule goes to quarantine once, with its page/row identity.

### 4.3 CIBC work

- Carry activity and portfolio sections across repeated `(continued)` headers.
- Parse ASCII/en/em-dash negative amounts consistently.
- Keep headers/footers out of transaction descriptions.
- Join multi-line security descriptions before resolving the instrument.
- Do not create cash or quantity zero when a token is missing.
- Keep mutual funds unresolved until a reviewed code exists.
- Add CAD/USD cash-equation and holdings-total fixtures.

### 4.4 HSBC work

- Merge main and continued pages for the same account before parsing.
- Preserve parentheses as negative signs.
- Parse activity descriptions without using the first word as a ticker.
- Distinguish regular closing balance from pending/after-settlement balances.
- Add fixtures for both suffix accounts and continuation layouts.

### 4.5 RBC work

- Parse the PDF as one account-period with CAD and USD child scopes.
- Use layout columns to assign debit/credit signs.
- Treat page continuations as part of an open row, not new verbs.
- Handle rows with blank activity cells without interpreting security names such as `BMO` or `MARKET...` as verbs.
- Parse and reconcile section/portfolio totals where printed.
- Keep annual reports separate from monthly checkpoints.

### 4.6 TD work

- Split on every full/legacy period header, including 2018–2022 bundled formats.
- Group repeated page fragments for the same period/account/currency into one logical statement scope.
- Ensure transaction dates fall in the correct month, with an explicit model for legitimate pending rows.
- Parse multi-line option holdings across harmless intervening headers/footers.
- Map stock splits to the canonical transaction type.
- Add a golden fixture for every bundled-layout generation.

### Phase 4 gate

- Zero duplicate emitted statement keys over the committed fixtures, 324 stored dumps, and 338 PDFs.
- RBC dual-currency statements retain both complete scopes.
- Every TD bundled month is emitted once with the correct dates.
- Every numeric zero in output is supported by a printed zero.
- Every candidate data row is parsed or quarantined with source provenance.

### Phase 4 completion record (2026-07-14)

- `PdfText` now retains raw page text plus optional `pdfplumber` words/visual
  lines, while text-only extraction receives deterministic page/line evidence
  rather than invented coordinates.
- Parser v2 state machines declare scoped snapshots, attach source spans, and
  quarantine unsupported numbers, incomplete option contracts, and
  out-of-period pending rows rather than manufacturing a value or invalid
  ordinary transaction.
- CIBC/HSBC continuation and cash handling, RBC CAD/USD aggregation, and TD
  full/legacy period splitting with repeated account fragments have committed
  regressions.
- The fixture audit, all 324 stored text dumps, and all 338 PDFs emitted zero
  duplicate statement keys and zero contract errors. The full PDF run produced
  337 parsed sources plus one explicit tax-document skip; the text-dump run
  produced 323 parsed sources plus one skip.
- Cash/position residuals remain reported by the read-only audit and are
  deliberately deferred to Phase 5; no balancing rows were created.

## Phase 5 — Implement real reconciliation

### 5.1 Position reconciliation formula

For each consecutive complete `(account, canonical instrument)` checkpoint:

```text
expected_close = previous_reported_quantity
               + SUM(position_delta within the interval)

residual = reported_close - expected_close
```

Store every contributing transaction. If a prior/current scope is incomplete, the result is `incomplete_input`, not a numerical pass/fail.

### 5.2 Cash reconciliation formula

Within each complete statement cash scope:

```text
expected_close = reported_opening_cash
               + SUM(cash_delta for that statement/currency)

residual = reported_closing_cash - expected_close
```

Also compare adjacent statement closing/opening balances. Use the broker's cash-effective date contract, not one global assumption.

### 5.3 Statement-total reconciliation

Where the PDF prints a portfolio/section total:

```text
reported securities total ~= SUM(parsed position market values)
reported portfolio total  ~= securities total + closing cash
```

Use broker/currency-specific tolerances only for documented rounding. Do not use a large tolerance to hide missing rows.

### 5.4 Status vocabulary

Use explicit statuses such as:

- `reconciled`;
- `within_rounding`;
- `unexplained_residual`;
- `incomplete_input`;
- `missing_prior_checkpoint`;
- `ambiguous_transfer`; and
- `not_applicable`.

### 5.5 Keep transfer pairing separate

Transfer pairing is a related but distinct problem. Match on canonical identity and signed deltas, not raw instrument IDs. Preserve conservative ambiguity handling. Journals such as DLR/DLR.U need an explicit reviewed relation rather than pretending two ticker keys are identical.

### Phase 5 gate

- Every complete checkpoint has a stored reconciliation result.
- Golden fixtures have zero unexplained residuals outside documented rounding.
- Live unresolved residuals are enumerated by statement/source span and never silently converted into adjustments.
- Re-running reconciliation is deterministic and does not duplicate results.

## Phase 6 — Replace Monthly/Performance reconstruction with one holdings service

### 6.1 One canonical `holdings_at()` service

Move holdings reconstruction out of route SQL into a tested domain service used by:

- `/monthly/snapshot` and `/monthly/diff`;
- Performance;
- Treemap/sector/correlation/RRG symbol selection; and
- any future exports.

### 6.2 Anchor by complete scope

- Choose the latest complete checkpoint per `(account, currency, position scope)` on or before the requested date.
- Reset only that scope; do not clear an entire account because one currency/partial section has a newer row.
- Group by canonical `instrument_key`.
- Apply normalized `position_delta` after the checkpoint.
- Reconstruct cash independently per `(account, currency)` from complete cash checkpoints plus `cash_delta`.

### 6.3 Return provenance and quality with each row

Include:

- canonical instrument key;
- checkpoint/source statement and checkpoint date;
- price and price date;
- reconstructed versus reported flag;
- reconciliation status; and
- incomplete/unpriced warning when relevant.

### 6.4 Correct valuation semantics

- On an exact statement checkpoint, prefer the broker-reported quantity/value.
- After a checkpoint, price the reconstructed quantity using the latest market price on/before the requested date when available.
- Clearly mark a stale checkpoint price fallback.
- Do not recompute book value as `old_avg_cost * new_quantity` after buys/sells unless a tested cost-basis engine supports it. Return unavailable/estimated status instead of a misleading exact value.

### 6.5 Fix diff and frontend keys

Use a stable key containing account ID, `instrument_key`, and currency. Do not use display symbol alone. Add stable React keys and preserve separate CAD/USD rows.

### 6.6 Performance uses the same state transitions

Remove the independent account-level “clear and forward-fill” interpretation in `performance.py`. Generate valuation checkpoints through the same complete-scope engine so Monthly, Performance, and visualization views cannot disagree about holdings.

### Phase 6 gate

- No duplicate logical rows in Monthly output.
- Post-checkpoint trades update the checkpoint row rather than creating unpriced duplicates.
- Same-symbol CAD/USD holdings remain distinct in snapshot and diff responses.
- Monthly, Performance, and visualization symbol sets agree for the same date/accounts.
- Every row explains whether it is broker-reported, reconstructed, or incomplete.

## Phase 7 — Rebuild safely in a shadow database

### 7.1 Never repair the live derived database in place

Because the current DB has tens of thousands of duplicate/orphan instruments and destructive statement overwrites, build a new database beside it, for example:

```text
data/ledger.sqlite          current live database, untouched
data/ledger.vnext.sqlite    corrected shadow rebuild
```

### 7.2 Preserve user-curated data explicitly

Export/import only reviewed or user-owned state:

- account nicknames/metadata;
- manual initial positions/cash;
- instrument aliases and reviewed fund identifiers;
- portfolio config; and
- any reviewed reconciliation annotations added by the refactor.

Do not copy broken derived transactions, snapshots, inferred initials, or automatic links as authoritative data.

### 7.3 Recompute inferred initials last

- Run inference only after instrument identities, snapshots, and reconciliation are correct.
- Infer only from a complete first checkpoint.
- Store the exact source checkpoint and calculation inputs.
- Preserve manual rows and never mix them with inferred output.

### 7.4 Produce a database comparison report

Compare old versus new by institution/account/period/currency:

- source and statement coverage;
- transaction, position, cash, and quarantine counts;
- recovered RBC/TD segments;
- unresolved identities;
- reconciliation statuses/residuals;
- latest holdings and totals; and
- user-curated rows preserved.

### 7.5 Manual source spot-checks

At minimum, inspect against the PDFs:

- every parser layout fixture source;
- every previously colliding RBC/TD format generation;
- all latest statements per account/currency;
- largest remaining position/cash residuals; and
- a stratified historical sample from each institution.

Every reported transaction must remain defensible against the source.

### 7.6 Cutover and rollback

- Stop the backend before cutover.
- Retain a timestamped backup of the old DB.
- Switch paths/rename only the database after the comparison gates pass and the user approves.
- Rollback is restoring the prior DB path; PDFs are never involved.

### Phase 7 gate

- A second clean rebuild produces the same active content hash.
- All curated state is accounted for.
- No PDF changed.
- Comparison and manual review have explicit sign-off.

## Phase 8 — Surface quality in the existing GUI and finish documentation

### 8.1 Extend the read-only Verify workflow

Keep database mutation CLI-only. Add read-only information to Verify:

- extraction completeness by statement/currency scope;
- cash, position, and total reconciliation status/residual;
- filters for unresolved/incomplete/unreconciled statements;
- source boxes for cash and summary totals as well as transactions/positions/quarantine; and
- parser/run version and active-run status.

### 8.2 Add quality indicators to Monthly

- Show checkpoint date and reported/reconstructed state.
- Warn when scope is incomplete, reconciliation is unresolved, or pricing is stale/missing.
- Keep native currency totals primary; show FX conversion date/rate.
- Add all new strings to i18n.

### 8.3 Update all relevant docs with each behavior change

After every phase, update the focused owner spec from Phase 0. At completion:

- update `CURRENT-STATE.md` from a fresh audit;
- update `USER-GUIDE.md` for visible quality states and commands;
- update parser-specific specs with verified layout quirks;
- update `README.md` only where the overview/quick start changed; and
- regenerate and verify `docs/index.html`.

## 5. Final acceptance criteria

The refactor is complete only when all of the following are true:

1. `AGENTS.md` is small, stable, and routes agents to focused on-demand specs.
2. Documentation describes current implemented behavior and known limits with no stale “DONE” claims.
3. Tests pass in a clean checkout without private ignored data.
4. The full corpus emits zero duplicate statement identities.
5. RBC CAD/USD and every TD bundled month survive ingestion.
6. A forced second ingest does not change active counts/content or grow instruments/quarantine.
7. One logical instrument has one canonical identity.
8. No parse failure is stored as zero.
9. Every active data row has source provenance.
10. Every complete cash/position checkpoint has an explicit reconciliation result.
11. Unresolved live residuals are visible and source-linked; none is balanced by fabrication.
12. Monthly emits unique canonical rows and uses complete scoped checkpoints.
13. Monthly, Performance, and visualization holdings agree for the same inputs.
14. Shadow rebuild, comparison, source spot-check, cutover, and rollback procedures are tested.
15. Required validation passes:

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

16. The corpus audit command passes its collision/idempotency gates and writes the final dated report under `logs/`.

## 6. Suggested commit sequence

Keep each commit reviewable and keep its owner docs in the same commit:

1. `docs: slim AGENTS and split truthful subsystem specs`
2. `test: add committed parser fixtures and corpus audit harness`
3. `db: add stable identities, scoped snapshots, runs, and reconciliation schema`
4. `ingest: stage validate and atomically activate source extractions`
5. `parser: fix CIBC and HSBC layout/state handling`
6. `parser: fix RBC dual-currency extraction and signs`
7. `parser: fix all TD bundled-period generations`
8. `reconcile: compute position cash and statement residuals`
9. `api: unify holdings reconstruction across API consumers`
10. `ui: surface extraction and reconciliation quality read-only`
11. `migration: build compare and validate the shadow ledger`
12. `docs: publish audited current state and regenerate docs site`

Do not combine the shadow database cutover with the parser/schema implementation commit. Cutover is an operational action after review, not an incidental side effect of deploying code.

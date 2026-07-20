# Verify Extraction Correctness and Usability Refactor Plan

**Status:** Implemented and structurally validated through Phase 5. Two clean
shadow builds and the geometry integrity audit pass; human PDF/UI review,
commit, and guarded cutover remain pending.

**Audit date:** 2026-07-19

**Scope:** Verify deep links, bidirectional row/PDF selection, statement page
ownership, extraction-quality explanations, reconciliation presentation, and
the persistence needed to support those behaviors.

This plan is based on the current code, schema-v9 live ledger, focused specs,
and a read-only inspection of the user-cited May 2026 TD USD statement. It does
not authorize a live-database mutation or a statement-PDF change.

## 1. Required outcome

After this work:

1. A source icon that is enabled always opens the intended statement, selects
   the intended persisted row, renders the page that owns it, scrolls only the
   PDF pane to the intended evidence rectangle, and visibly highlights it.
2. Clicking a transaction, position, cash, summary, or quarantine row on the
   right scrolls the left PDF pane to its evidence when exact geometry exists.
3. Clicking an evidence rectangle on the left selects and reveals the matching
   row on the right without moving the left PDF away from the clicked rectangle.
4. Verify renders only physical PDF pages assigned to the selected logical
   statement, even when one PDF contains several accounts, currencies, or
   periods. Page assignments may overlap when two statements genuinely share a
   physical page; they must never be guessed from a date or account name.
5. The top of the right pane contains one concise statement status. Parsed
   transactions, positions, and cash follow immediately. Detailed quality,
   reconciliation, quarantine, and parser diagnostics are at the bottom.
6. An incomplete status says what input is missing or untrusted and links to
   the evidence that caused it. Repeated per-instrument results do not obscure
   one scope-wide extraction problem.
7. A reconstructed Monthly holding is described as a calculation with several
   sources. It is never represented as though one extracted PDF row were the
   source of the reconstructed quantity.

## 2. Findings: why the current behavior fails

### 2.1 Deep-link state is applied too late and can lose a race

`SourceLink.tsx` correctly writes `statement=<id>&ref=<kind>:<id>`, and the
transaction table currently supplies matching transaction and statement IDs.
The failure is inside `Verify.tsx`:

- the URL is copied into React state in an effect;
- a separate effect defaults the page to the newest statement; and
- if the statements React Query cache is already populated when Verify mounts,
  both effects can run in one commit and the default selection can replace the
  requested statement.

The URL is therefore not the authoritative initial selection. The page also
does not validate that the requested reference belongs to the requested
statement, and it gives no useful message when the reference exists but has no
linkable geometry.

There is a second contract violation: Transactions and Monthly show a source
icon from row IDs alone. They do not know whether `source_evidence_lines`
contains a drawable link. In the current live ledger, read-only counts show:

| Row kind | Rows with no linked PDF rectangle |
|---|---:|
| transaction | 145 |
| position | 227 |
| cash | 646 |

Those rows can currently display an icon that promises a jump the system
cannot perform. Unmatched or ambiguous evidence must remain visible as a data
quality state, but it must not masquerade as an exact link.

### 2.2 PDF scrolling occurs before layout is stable

The current selection effect considers a page ready when its container ref
exists. That is earlier than PDF.js finishing the canvas and overlay. Before
rendering, preceding canvas elements have placeholder/default heights; as they
expand, the selected page moves. The effect records the selection in
`lastScrolledSelection` before the final layout is stable, so later render
notifications cannot correct the position.

The scroll itself uses two competing smooth operations:

1. `element.scrollIntoView()`, which may scroll the PDF pane and outer document;
2. an immediate `pdfScrollRef.scrollBy()` fine adjustment.

The two animations race. `scrollIntoView()` also affects scrollable ancestors,
which is inappropriate for two independently scrolling panes.

Selection has no origin. A click on the left PDF invokes the same effect as a
click on a right-side row, so selecting a box causes the PDF pane to scroll
itself again. Conversely, the code keeps no DOM refs for right-side items, so a
left click cannot reliably reveal an off-screen row in the right pane. Finally,
deselecting and reselecting the same key may not scroll because the last-scroll
guard is never reset for that key.

### 2.3 The API is source-file scoped where the UI is statement scoped

The database already gives `quarantine_transactions` a `statement_id`, but
`_load_statement_rows()` queries quarantine by `source_file_id`. Every logical
statement in a multi-statement PDF therefore receives every quarantine row
from that PDF.

The read-only TD spot check makes the defect concrete:

- the selected USD statement owns 10 quarantine rows;
- the Verify detail payload returns 21, because 11 rows from the CAD statement
  in the same PDF are included; and
- the boxes response returns all 10 physical PDF pages rather than the pages of
  the selected logical statement.

`_persisted_boxes()` deliberately loads every `source_pages` row for the source
file. `PdfView` then assumes `data.pages.length` means physical pages
`1..length`. This representation cannot express a statement that owns pages
`[5, 6, 8]`, and it cannot exclude pages belonging to another statement.

### 2.4 The schema does not record statement-to-page ownership

`statements` identifies a source, account, period, and type, but it has no page
range or page-membership relation. `source_pages` belongs to the source and
geometry extractor, not to a logical statement. Inferring a statement's pages
from the minimum and maximum evidence page is unsafe because:

- a statement page can contain no emitted financial row;
- pages for accounts/currencies can be interleaved or repeated;
- more than one statement can legitimately share a page; and
- an incorrectly attached evidence hint would make the inferred boundary
  self-confirming.

The current parsers operate mainly on `pdf.full_text`. Their split helpers lose
the original character-to-page range. `attach_source_spans()` then performs a
post-parse, source-wide text lookup. Its cursor is keyed by raw text, not by
statement/page ownership. Repeated lines can consequently be assigned to the
wrong substatement before geometry enrichment begins.

The TD spot check contains suspicious USD quarantine page hints on pages also
used by the CAD statement. Those hints must be re-established from a page-aware
parse and source review, not accepted as page ownership merely because they are
already persisted.

### 2.5 The geometry bridge is too strict in one place and too trusting in another

Semantic ingest records line numbers from plain `extract_text()` output.
Layout enrichment constructs visual lines independently from words. Those two
line-number systems are not guaranteed to align, but the current matcher
requires both page and line to accept a repeated exact match as hinted.

The matcher also requires every line in one evidence record to be contiguous.
Cash evidence normally contains two source lines—printed opening and printed
closing balance—that are intentionally separated by the activity table. The
live geometry audit reflects this systematic mismatch: 645 of 648 cash rows
are `unmatched`, while their semantic evidence is present.

At the other end, a persisted page/line hint can be trusted even if the parser
assigned that hint through the source-wide post-hoc locator. Geometry cannot
repair incorrect semantic ownership; it can only draw what it was told.

The schema already has `source_evidence_lines.ordinal`, `token_start`, and
`token_end`, but enrichment leaves token ranges null and the API merges refs
into whole-line rectangles. `BoxDiv` chooses `line.refs[0]` on a left click.
That is arbitrary whenever one visual line supports more than one logical
reference.

### 2.6 The quality panel discards information already in the database

`reconciliation_results` already stores `instrument_id`, checkpoint dates,
equation inputs, residual, reason, and components. The statement API omits the
instrument ID/symbol, checkpoint dates, result subtype, and component count.
The UI renders the reason only as a hover title and displays the internal
`scope_key` (`default`) as though it explained the issue.

For the inspected TD statement, the actual persisted facts are:

- the USD cash activity equation is reconciled;
- cash continuity with the prior statement is reconciled;
- the positions scope is `unknown`/warning;
- nine statement-owned rows are quarantined as unrecognized holdings;
- one is quarantined because no printed symbol was captured; and
- each affected position equation repeats `current position scope is not
  complete`.

The UI instead shows many visually identical “Position · USD · default” rows
with “Incomplete input.” It neither identifies the instrument nor connects the
scope-level blocker to the ten quarantined rows.

### 2.7 Scope completeness has a status but no persisted explanation

`snapshot_sets` stores `completeness` and `validation_status`, but not the one
or more findings that caused a scope to be `partial`/`unknown`. Quarantine rows
link to a statement and evidence, but not to the snapshot scope they prevent
from becoming complete. The application therefore cannot answer “exactly what
is incomplete?” without guessing from nearby quarantine rows.

This is the main persistence gap for useful extraction-quality reporting.

### 2.8 A Monthly row may be computed rather than extracted

For a reported complete checkpoint, `holdings_at()` returns an exact position
or cash source row. For a later reconstructed quantity, the current
`source_ref` points only to its earlier checkpoint. The displayed quantity may
also depend on several later transactions. A single icon labelled as the
source of “this row” is therefore misleading even when it lands on the correct
checkpoint box.

The target contract must distinguish:

- a reported row with one exact extracted source;
- a reconstructed row supported by a checkpoint plus movements;
- an incomplete row with a latest observed-but-untrusted scope; and
- a row for which no defensible source geometry exists.

## 3. Persistence changes proposed

Use a schema-version bump and a clean shadow rebuild. Do not patch page
ownership or quality reasons into the current live derived rows by inference.

### 3.1 Add semantic statement page membership

Add a table independent of replaceable geometry:

```sql
CREATE TABLE statement_pages (
    statement_id      INTEGER NOT NULL
                      REFERENCES statements(statement_id) ON DELETE CASCADE,
    page_number       INTEGER NOT NULL CHECK (page_number >= 1),
    assignment_method TEXT NOT NULL CHECK (assignment_method IN
                      ('parser_explicit', 'single_statement_source')),
    PRIMARY KEY (statement_id, page_number)
);
```

Rules:

- a parser emits an explicit ordered set of physical one-based page numbers;
- a single-statement source may claim all pages through the explicit
  `single_statement_source` method;
- a multi-statement source must use `parser_explicit` membership;
- overlap is allowed; empty membership is invalid for a parsed monthly
  statement; and
- validation checks every page is within `source_files.page_count` and every
  row's page hint is a member when that row has a hint.

Do not add `first_page`/`last_page` columns: a range cannot represent
non-contiguous or shared pages.

### 3.2 Persist scope-blocking extraction findings

Add a normalized `snapshot_scope_issues` table rather than one free-text column
on `snapshot_sets`:

```text
scope_issue_id, deterministic issue_key, snapshot_set_id,
issue_code, severity, detail_json, blocks_completeness,
evidence_id (optional), quarantine_id (optional)
```

The issue code is stable and machine-readable; `detail_json` contains only
structured, non-localized parameters such as a count or missing field name.
The API/UI turns the code and parameters into translated human-readable text;
parser exception detail remains technical diagnostics at the bottom. Multiple
issues may belong to one scope. Evidence and quarantine links make the
explanation source-selectable. Examples include:

- `unrecognized_holding_row`;
- `holding_identity_missing`;
- `unsupported_numeric_activity`;
- `closing_balance_missing`;
- `section_not_fully_recognized`; and
- `parser_section_error`.

Extend the parser contract with `ParsedScopeIssue` and make
`ParsedSnapshotSet.issues` explicit. A parser that marks a scope incomplete
must emit at least one blocking issue. Validation rejects contradictory output
such as `complete` plus a blocking issue.

### 3.3 Make reconciliation result semantics explicit

Keep the existing equation columns and component table. They are sufficient
for arithmetic audit. Add two structured fields to `reconciliation_results`:

- `check_type`: for example `position_rollforward`, `cash_activity`,
  `cash_continuity`, `position_total`, `cash_total`, or `portfolio_total`;
- `reason_code`: a stable code such as `current_scope_incomplete`,
  `prior_scope_missing`, `statement_period_gap`, `movement_identity_missing`,
  or `residual_outside_tolerance`.

Retain `reason` for specific details and backward-readable reports. The API
must not parse `reconciliation_key` to decide what an equation means.

### 3.4 Do not add redundant data

The following are already present and should be projected or joined instead of
duplicated:

- transaction/position/cash/quarantine IDs and their statement/evidence links;
- `reconciliation_results.instrument_id` and equation values;
- `reconciliation_components` and their transaction evidence;
- replaceable page/line geometry and evidence-to-line links; and
- `quarantine_transactions.statement_id`.

No new alias, instrument, or balancing-adjustment table is needed for this
work.

## 4. Parser and evidence design

### 4.1 Preserve page ranges while splitting text

Introduce a shared page-indexed text view that joins `PdfText.pages` while
retaining each page's character range. Parser chunk/subaccount helpers return
fragments with both text and physical page membership instead of naked string
slices.

Update CIBC, HSBC, RBC, and TD to accumulate fragment pages on each
`ParsedStatement`. TD's repeated account fragments fixture is the first golden
case: CAD and USD statements in one PDF must receive their own page sets, and a
repeated continuation page must remain with the correct account/currency.

Annual or one-statement files may explicitly claim every page. No parser may
derive pages from the final evidence minimum/maximum.

### 4.2 Constrain source-span lookup to statement pages

Refactor `SourceLocator` so each statement gets a locator constrained to its
declared page membership. Prefer assigning spans while a parser still holds its
page-aware fragment; use the constrained locator only as the common fallback.
Repeated raw text outside the statement's pages must not be considered.

If more than one candidate remains inside the allowed pages and there is no
defensible occurrence/context discriminator, emit an ambiguous/unlinked span.
Never select the first global occurrence to make a link available.

### 4.3 Match semantic fragments to geometry in order

Change layout enrichment to use this order:

1. restrict candidates to the statement's declared pages;
2. use the semantic page hint as the primary locator;
3. accept a line-number hint only when its extraction coordinate system is
   compatible;
4. match exact contiguous lines when present;
5. for multi-fragment evidence such as cash opening/closing, match a unique
   monotonic sequence of exact lines even when non-contiguous;
6. use a unique ordered token sequence as a fallback; and
7. retain `ambiguous`/`unmatched` when uniqueness is not proven.

Populate `token_start`/`token_end` when the evidence occupies a subset of a
visual line. Compute the drawable rectangle from the selected word slice;
otherwise use the whole persisted source-line box.

Geometry remains rebuildable and excluded from the semantic content hash.
Every enrichment still verifies the immutable PDF SHA-256 before replacing
geometry.

## 5. API contract changes

### 5.1 Return statement-owned rows only

For `GET /statements/{id}/boxes`:

- query quarantine with `q.statement_id = :statement_id`;
- return source-level/unassigned quarantine separately, never copied into every
  statement;
- return only pages in `statement_pages`;
- preserve each page's original physical `page_number`; and
- validate every requested ref belongs to the statement before presenting it
  as selected.

The raw PDF endpoint can continue serving the immutable full PDF. PDF.js can
load it once and render only the original page numbers returned by the boxes
endpoint. No cropped derivative PDF is required.

### 5.2 Return per-reference evidence boxes

Replace the current line-centric `LineBox { refs[] }` presentation payload with
an evidence-centric shape conceptually like:

```text
page_number, width, height,
boxes: [{ref, evidence_id, rect, ordinal, geometry_status,
         match_method, confidence}]
```

One reference can have several ordered rectangles. A rectangle normally maps
to one reference. If two references genuinely share the same evidence, expose
that explicitly and make the frontend offer/cycle the candidates; never choose
array element zero silently.

### 5.3 Make source links server-authoritative

Transactions and Monthly should consume server-produced source/provenance
objects, not construct a promise of linkability from numeric IDs alone. A
source ref includes at least:

```text
statement_id, kind, id, geometry_status, page_numbers, linkable
```

Only exact or uniquely token-matched geometry is linkable. Ambiguous,
unmatched, and coordinate-free rows keep a disabled source indicator with an
explanation, or no icon according to the existing Settings preference.

For Monthly, return a provenance object:

- `reported_row`: one exact position/cash ref;
- `checkpoint_plus_movements`: checkpoint ref plus contributing transaction
  refs used after it;
- `observed_incomplete`: optional current statement row that was observed but
  not trusted as a checkpoint; or
- `unavailable`.

The source icon for a reconstructed row opens a provenance context and labels
the checkpoint/movements. It must not claim that the checkpoint quantity is
the reconstructed quantity.

### 5.4 Return useful reconciliation detail

Project the data already stored:

- instrument ID, full broker instrument identity, and display label;
- `check_type` and `reason_code`;
- prior/current checkpoint dates;
- opening, summed deltas, expected close, reported close, residual, tolerance;
- component count and source-linked component transactions; and
- linked snapshot-scope issues.

Group scope-wide blockers in the response so the UI can say, for example,
“Positions in USD were not reconciled because 10 holding rows require review,”
then reveal the linked findings. Keep per-instrument rows available in an
expanded detail view; do not discard audit facts.

## 6. Frontend interaction design

### 6.1 Make URL selection authoritative

Parse and validate the URL into the initial selection before default-statement
logic runs. Use one selection model:

```text
{ statementId, refKey, origin, requestToken }
origin = deep_link | right_list | pdf_box | statement_change
```

Changing the statement clears an incompatible ref. A deep link is not replaced
by the newest-statement default. Repeating the same selection creates a new
request token so it can scroll again.

### 6.2 Use pane-local, origin-aware scrolling

- Reserve every page's final scaled width/height from API page metadata before
  PDF.js renders the canvas.
- Track readiness per physical page and overlay, not one global render count.
- For `deep_link` or `right_list`, use one
  `pdfPane.scrollTo(targetOffset)` after the target page/overlay is ready.
- Never call `scrollIntoView()` for PDF pages.
- For `pdf_box`, leave the PDF scroll position unchanged and scroll only the
  right items pane to the selected row.
- Store DOM refs for right-side rows and use their offset relative to the right
  pane, not the outer document.
- Cancel or replace an in-flight smooth scroll when a new selection arrives.

### 6.3 Render physical page numbers, not `1..pages.length`

`PdfView` receives an ordered list of page metadata and calls
`doc.getPage(page.page_number)`. Rendering pages `[5, 6, 8]` must not render
pages 1–3 or reinterpret page 5 as page 1. Display the physical page number so
reviewers can compare it with the PDF.

### 6.4 Reorder and simplify the right pane

Target layout:

```text
+--------------------------------------------------+
| Status: Needs review — positions incomplete     |
+--------------------------------------------------+
| Transactions                                    |
| Positions / holdings                            |
| Cash                                            |
| Summary totals                                  |
|                                                  |
| Data quality and reconciliation (bottom)        |
|   - human-readable scope findings               |
|   - grouped equations, expandable detail        |
|   - parser/run metadata in collapsed diagnostics|
| Quarantine / unresolved rows (bottom)           |
+--------------------------------------------------+
```

The status strip is the only quality block at the top. It gives the most severe
state and a short cause, such as “Needs review: 10 holding rows were not
recognized; cash reconciles.” Internal `default` scope names stay hidden unless
there are multiple meaningful scopes.

Transactions and positions show enough identity to distinguish similar rows:
quantity, price/amount, currency, and full option contract where applicable.
Reconciliation rows display the instrument and equation; reasons are visible
text, not hover-only titles. All new user-visible text goes through i18n.

## 7. Implementation phases and review gates

### Phase 0 — Lock the failing contracts in tests

Add tests before behavior changes:

- a multi-statement source where statement A quarantine/boxes cannot appear in
  statement B;
- non-contiguous physical page membership;
- a URL deep link with a pre-populated React Query cache;
- right-row click scrolls only the PDF pane;
- left-box click reveals the right row without changing PDF scrollTop;
- selection waits for stable page dimensions/overlay readiness;
- deselect/reselect of the same ref scrolls again;
- a non-contiguous cash opening/closing evidence pair links both boxes; and
- linkability is false for ambiguous/unmatched geometry.

Introduce a frontend test runner suitable for React state/DOM behavior
(Vitest + React Testing Library/jsdom is sufficient for unit interaction tests).
Keep one browser-level regression for the two-scroll-pane behavior with PDF.js
mocked or a synthetic committed PDF. No private statement text enters fixtures.

**Gate:** the new regressions fail for the intended current reasons, existing
tests still pass, and the test design is reviewed before schema work.

### Phase 1 — Add page ownership and scope findings

- bump the parser contract and SQLite schema version;
- add `ParsedStatement.page_numbers` and `ParsedScopeIssue`;
- add `statement_pages`, `snapshot_scope_issues`, reconciliation `check_type`,
  and `reason_code`;
- update validation and deterministic content hashing/counts;
- update source activation/replacement cascades and migrations; and
- treat old rows without trustworthy page membership/findings as unavailable,
  not inferred complete.

**Gate:** schema/FK/integrity tests pass; invalid page membership and incomplete
scopes without reasons are rejected; no live database is changed.

### Phase 2 — Make all parsers page-aware

- implement the shared page-indexed text view;
- update CIBC, HSBC, RBC, and TD split/state code to retain fragment pages;
- constrain evidence lookup to statement pages;
- emit scope issues at the point a row or section prevents completeness; and
- extend synthetic fixtures for multi-account, multi-currency, bundled-period,
  and continuation-page cases.

Use the user-cited TD statement only as a private local spot check. Confirm its
actual USD physical pages and ten currently reported blockers against the PDF;
do not encode its account number, path, or raw text in commits.

**Gate:** every parsed statement has defensible page membership; every
incomplete scope has source-linked findings; cross-statement evidence leakage
is zero in fixtures and the local corpus audit.

### Phase 3 — Repair geometry matching and API projection

- implement page-constrained, ordered non-contiguous fragment matching;
- populate word-token ranges and evidence-specific rectangles;
- make statement detail queries strictly statement-owned;
- return only declared statement pages;
- expose server-authoritative linkability/provenance;
- include instrument/equation/component/scope-issue reconciliation detail; and
- add API tests for refs, page numbers, quarantine isolation, and grouped
  explanations.

**Gate:** every enabled fixture link resolves to exactly the requested ref;
ambiguous/unmatched links remain disabled and explicit; cash opening/closing
fixture evidence is linkable without fuzzy guessing.

### Phase 4 — Rebuild Verify interactions and information hierarchy

- implement authoritative deep-link state and selection origins;
- reserve page geometry before rendering and use pane-local scroll math;
- add right-row refs and bidirectional reveal behavior;
- render only original statement page numbers;
- place transactions, positions, cash, and summary first;
- add the single top status strip;
- move detailed quality/reconciliation/quarantine to the bottom;
- group repeated scope-wide failures and expose expandable equations; and
- distinguish reported versus reconstructed Monthly provenance.

**Gate:** frontend unit/browser interaction tests pass at desktop and the
existing single-column responsive breakpoint; manual clicks confirm neither
pane moves unexpectedly.

### Phase 5 — Shadow rebuild, private source review, and documentation

Because page membership and scope findings are semantic parser output, rebuild
into a fresh shadow database; do not fabricate them for active rows in place.

1. build the full shadow twice and compare content fingerprints;
2. verify the immutable PDF manifest before/after;
3. run layout enrichment on the shadow;
4. publish counts by geometry status and row kind;
5. spot-check deep links from Transactions and Monthly across all four brokers,
   including reported, reconstructed, incomplete, option, cash, ambiguous, and
   multi-statement cases;
6. inspect the user-cited TD statement for correct pages, row ownership, cash
   equations, and explicit position blockers;
7. obtain human sign-off before guarded cutover; and
8. retain the rollback database per `spec/OPERATIONS.md`.

Update the owning documentation with the implementation:

- `spec/DATA-MODEL.md`;
- `spec/PARSER-CONTRACT.md`;
- `spec/INGESTION.md`;
- `spec/API-UI.md`;
- `spec/RECONCILIATION.md`;
- affected `spec/parsers/*.md` files;
- `spec/USER-GUIDE.md`;
- `spec/CURRENT-STATE.md` only from the new measured audit; and
- generated `docs/index.html`.

`AGENTS.md` should remain small. Its existing routing and source-traceability
rules already cover this work; add no implementation detail unless a new
stable repository-wide invariant is discovered.

## 8. Acceptance criteria

The change is complete only when all are true:

1. An enabled source icon has a server-provided linkable ref, and that exact
   ref exists in the selected statement's boxes payload.
2. Transactions and reported Monthly rows deep-link to their exact persisted
   row; reconstructed Monthly rows open an explicitly labelled provenance
   chain rather than pretending to be one extraction.
3. A cached Verify mount cannot override a URL-requested statement/ref.
4. Right-to-left selection lands with the target rectangle visible after PDF
   rendering stabilizes.
5. Left-to-right selection keeps the PDF pane at its clicked scroll position
   and reveals the matching right-side row.
6. Repeating the same selection works; rapid selection changes do not finish
   an older scroll request afterward.
7. The statement boxes API returns only `statement_pages`, preserves physical
   page numbers, and never leaks another statement's quarantine rows.
8. Every incomplete scope has at least one persisted source-linked issue; the
   UI states the issue in plain language.
9. Reconciliation detail identifies the instrument/check type and shows the
   equation or the exact unavailable input. Scope-wide blockers are grouped.
10. Cash evidence can link non-contiguous opening/closing lines when the ordered
    match is unique; otherwise it remains explicitly ambiguous/unmatched.
11. No page, evidence row, symbol, amount, or reconciliation input is inferred
    merely to make a link or equation look complete.
12. The full private corpus audit reports statement-page ownership conflicts,
    cross-statement evidence leakage, geometry status by row kind, and refs
    advertised without boxes; all hard counts are zero except explicitly
    reviewed ambiguous/unmatched evidence.
13. Required checks pass:

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
uv run python scripts/build_docs.py
uv run python scripts/build_docs.py --check
```

14. Two clean shadow builds match, private source spot checks are signed off,
    and live cutover remains a separate guarded operation.

## 9. Suggested commit sequence

Keep implementation and review boundaries small. After this plan is approved:

1. `test: lock verify navigation and statement-page regressions`
2. `db: add statement pages and scoped extraction findings`
3. `parser: preserve statement page ownership and issue evidence`
4. `ingest: make geometry matching statement-aware`
5. `api: expose exact verify refs provenance and explanations`
6. `ui: fix verify selection scrolling and panel hierarchy`
7. `docs: publish verify and provenance contracts`
8. separate reviewed shadow rebuild/cutover operation

Commit/push and source-review gates should occur after each approved phase, not
as one unreviewable change.

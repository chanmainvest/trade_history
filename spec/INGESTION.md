# Ingestion

This page documents the current PDF-to-SQLite path and its known activation
semantics. Institution parsing details are under `spec/parsers/`.

## Inputs and extraction

`ledger ingest run` walks only `*.pdf` files directly below each directory in
`STATEMENTS_DIR`. Folder names map to institution codes in `config.INSTITUTIONS`;
unknown folders use their literal name as the code.

`pdf_text.extract_pdf()` reads text and page dimensions with `pdfplumber`,
falls back to `pypdf` only when the first result is empty, fingerprints the
file, and marks fewer than 20 extracted characters as image-only. Normal ingest
requests page words only for RBC, whose semantic debit/credit columns cannot be
recovered from plain text; other parsers remain text-first. Persisted Verify
geometry is still a separate rebuildable pass. OCR is not implemented.

PDFs are immutable inputs. Text dumps under `<DATA_DIR>/text_dumps/` and logs
are derived artifacts.

## Current flow

```text
discover path
  -> hash and contract-aware cache check (unless --force)
  -> extract text
  -> skip image-only, explicit non-broker documents / fail unclaimed
  -> first registered parser whose can_handle() returns true
  -> parser.parse(PdfText)
  -> enrich explicit old/new ticker-change pairs
  -> validate the complete ParseResult
  -> conservatively resolve printed identities
  -> stage one source in a SQLite savepoint
  -> atomically activate the fully written source run
  -> pair transfers, rebuild movement links, and persist checkpoint equations
  -> regenerate derived ingestion audit indexes
```

The registered parsers are CIBC, HSBC, RBC, and TD. CIBC, RBC, and TD report
`2.6.0`; HSBC reports `2.5.0`. Parser, contract, schema, and resolver changes
intentionally invalidate older active cache entries so a reviewed re-ingest
exercises the current extraction contract.

## Independent layout enrichment

After semantic rows have been reviewed and activated,
`ledger ingest enrich-layout` reopens immutable PDFs with word geometry
enabled. It verifies each PDF's SHA-256, replaces only derived geometry for the
active run, and links stored semantic evidence to exact PDF lines. Matching
first restricts candidates to the owning statement's explicit physical pages.
It accepts a unique persisted page hint, a compatible page/line hint, a unique
exact line sequence, a unique ordered non-contiguous sequence (for example cash
opening/closing lines), or one unique contiguous token sequence. Token matches
persist the supporting word slice. Repeated candidates are stored as
`ambiguous`; unmatched text and PDFs without coordinate lines remain explicit
statuses. It never changes a transaction, amount, quantity, instrument, scope,
or semantic evidence key.

Use `--source-file-id ID` to rebuild one source. The command is intentionally
CLI-only. A geometry failure rolls back that source's geometry savepoint and
does not affect active semantic data.

## Status and cache behavior

`source_files.parse_status` is a compatibility summary of the active
extraction. It is `pending`, `ok`, `partial`, `failed`, or `skipped`. A parser
can explicitly return a skipped result for a non-broker document such as a tax
summary; no statement activation is attempted. A failed or skipped attempt is
recorded in `ingestion_runs`; if a source already has an active extraction, its
active metadata and pointer remain unchanged.

An unchanged source skips only when its active run matches all of the
following: source SHA-256, current registered parser name/version, parser
contract version, SQLite schema version, and the resolver version. The resolver
version includes a deterministic fingerprint of reviewed aliases and resolved
fund-code lookups, so changing reviewed identity data makes source output stale
without requiring `--force`. `--force` remains an explicit override.

## Persistence behavior

Fatal validation issues record a failed source attempt and skip every statement
write from that parser result. The same applies to parser crashes and activation
errors. The previous active extraction remains visible. Validation covers
duplicate statement identities, parser errors, periods/transaction dates,
transaction vocabulary, currencies, finite numerics, option identity, and
source-span/scope declarations. An undeclared scope remains a visible warning
and becomes an `unknown` persisted set rather than a clearing checkpoint.

Explicit `NAME/SYMBOL/TICKER CHANGE ... FROM <old> TO <new>` rows are enriched
before validation. Activation resolves and writes both printed instruments,
the dated 1:1 relationship, and the source transaction/evidence in the same
savepoint. Rows without both printed symbols are not guessed.

For one valid source, the pipeline first checks every emitted statement/source
row key in memory, then opens one SQLite savepoint. It creates a `validated`
run; resolves identities; replaces the source's old *derived* run only inside
that uncommitted savepoint; writes every statement, evidence row, normalized
delta, scope, and child row under the new run; records a deterministic content
count/hash; switches the source pointer; and commits. SQLite's v6-and-later global
statement/evidence identities mean the old derived rows are deleted just before
new child writes rather than held side-by-side, but no reader can observe that
intermediate state. Any exception rolls back to the prior committed source.

The result is source-wide replacement rather than statement-by-statement
replacement:

- statements omitted by a newer successful parse are removed with the old run;
- repeated forced ingest of the same resolved output keeps active counts and
  content hash stable; and
- a parser/validation/write failure cannot partially fan out into active rows.

When one complete snapshot prints several distinct lots for the same canonical
instrument, persistence sums quantity/book/market/P&L values whose components
are known. It keeps one exact duplicate raw row once, never overwrites an
earlier distinct lot with the last row, and clears average cost when aggregation
cannot preserve one reported value.

`ingestion_runs` retains failed attempts for audit. Successful replaced runs are
derived output and are removed with their source children; active run content
hash/counts are the authoritative current-extraction record.

## Identity resolution

Resolution runs inside the same source savepoint, after parser validation and
before persistence. It preserves the parser's printed identity and applies
these deterministic steps:

1. retain a complete printed option contract before considering its underlying
   ticker in the listing catalog;
2. resolve an exact institution/currency entry in the reviewed listing catalog;
3. match an exact reviewed alias, previously resolved candidate, reviewed fund
   lookup, or uniquely known database listing;
4. retain a genuinely ticker-shaped printed symbol;
5. match one unambiguous exact identity in the same statement's holdings; or
6. queue the public security name in `instrument_resolution_candidates` and
   mark the financial row `unresolved_printed_identity` with zero confidence.

Compact company/fund descriptions such as `BCEINC`, `NUTRIENLTD`, and
`ISHARESIBOXX...` are not accepted merely because they satisfy a permissive
symbol regex. Transaction rows
store the selected method/confidence and a resolution evidence link when a
source span is available. An unresolved transaction remains auditable with a
null instrument; its printed-name token is never persisted as a ticker. An
unresolved position is moved to quarantine and its complete scope is downgraded
to `unknown`, because a checkpoint cannot safely identify that holding.
Resolved instrument rows retain their identity provenance.
This ordering is material: resolving `PUT NTR ...` as the NTR equity would
erase expiry/strike/type and create a false negative stock holding.
`ledger ingest repair-symbols` remains a legacy/manual maintenance command for
old derived data, but normal ingest no longer invokes it.

`ledger ingest resolve-instruments` applies the deterministic catalog to an
older derived ledger and reports pending candidates/Yahoo mappings. Conflicting
checkpoint rows are not merged; use a clean shadow re-ingest for those. With
`--verify-yahoo`, the command sends only public security names/symbols (never
account/source values), requires one strong unique search match in the expected
currency/listing family plus non-empty price history, and records
verified/failed/ambiguous status. A newly resolved candidate affects financial
rows only on the next deterministic re-ingest.

Yahoo verification is not part of `ledger ingest run`: source activation must
remain reproducible and must not depend on network availability. The resolver
cache includes the catalog version, resolved candidates, and provider mappings,
so a verified resolution makes affected sources stale for re-ingest.

## Post-processing

After a scan, `reconcile_after_ingest()` first rebuilds conservative derived
instrument links for name-only buys/sells from canonical holding names in the
same native currency. It then pairs unambiguous transfers, recreates
position/transaction attribution links for complete scopes, and rebuilds
generated position, cash, and statement-total results. Automatic name links
change only `instrument_id` and resolution provenance on rows already marked
`unresolved_printed_identity`; they never change a reported quantity, amount,
description, checkpoint, or balance. The equation rebuild replaces only its
`recon:v1:*` derived rows. A limited scan still finishes this active-output
maintenance rather than returning mid-command.

If several PDFs describe the same account, period, and statement type, every
source remains available in Verify. Derived initials, holdings, transaction
lists, transfer pairing, and reconciliation use only the most recently
persisted statement revision, so a duplicate/reissued PDF cannot double a
movement.

## Logs

- standard logs are configured under `logs/`;
- `logs/ingestion_attempts.jsonl` is regenerated from persisted run metadata;
- `logs/skipped_pdfs.log` is regenerated from latest skipped attempts;
- `logs/quarantine.jsonl` is regenerated from active quarantine rows;
- market commands append to `logs/market_scrape.jsonl`.

The regenerated ingestion indexes are deterministic JSONL and contain source,
run, row/evidence IDs, and reason/status but no raw statement text. Standard
application logs and market scrape events retain their separate logging
contracts.

## Read-only extraction audit

`ledger audit extraction` accepts a PDF tree or stored text-dump tree, selects
and runs parsers without opening SQLite, applies the same validator, calculates
cash and logical-instrument position residuals where possible, measures raw-line
coverage, and overwrites a deterministic JSONL report. It excludes raw statement
text from the report.

On 2026-07-14 parser v2 audited all 324 stored text dumps (323 parsed, one
explicit tax-document skip) and all 338 PDFs (337 parsed, one skip). Both runs
had zero invalid/unclaimed/failed sources, zero contract errors/warnings, and
zero duplicate statement keys. They still report cash/position residuals and
incomplete cash scopes; those remain source-quality findings, not grounds for
fabricated parser rows. A ledger reconciliation run persists its own
source-linked status records, while the parser audit remains read-only. Current
counts are maintained in
[CURRENT-STATE.md](CURRENT-STATE.md).

The 2026-07-12 Phase 1 run is retained only as the pre-v2 defect baseline: it
reported 178 duplicate statement keys across the PDF audit. It is not evidence
about current parser output.

## Remaining work

The source activation boundary and parser v2 layout/state handling are
implemented for the committed fixture corpus. The reconciliation command also
persists scoped position, cash, and statement-total equations, and Monthly,
Performance, and Visualisations share the canonical holdings service.

`ledger shadow build` now provides the safe real-data migration path: it parses
into a fresh target, preserves reviewed/user-owned state explicitly, verifies a
second clean rebuild, and writes a redacted comparison report. The live
database remains untouched until a human completes source spot checks and runs
the separate guarded cutover command. See [OPERATIONS.md](OPERATIONS.md) and
the plan for the current review/cutover status.

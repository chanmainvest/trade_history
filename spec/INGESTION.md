# Ingestion

This page documents the current PDF-to-SQLite path and its known activation
semantics. Institution parsing details are under `spec/parsers/`.

## Inputs and extraction

`ledger ingest run` walks only `*.pdf` files directly below each directory in
`STATEMENTS_DIR`. Folder names map to institution codes in `config.INSTITUTIONS`;
unknown folders use their literal name as the code.

`pdf_text.extract_pdf()` reads all pages with `pdfplumber`, retains its
page-local words/visual lines when available, falls back to `pypdf` only when
the first result is empty, fingerprints the file, and marks fewer than 20
extracted characters as image-only. OCR is not implemented.

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
  -> validate the complete ParseResult
  -> conservatively resolve printed identities
  -> stage one source in a SQLite savepoint
  -> atomically activate the fully written source run
  -> pair transfers, rebuild movement links, and persist checkpoint equations
  -> regenerate derived ingestion audit indexes
```

The registered parsers are CIBC, HSBC, RBC, and TD, all currently reporting
version `2.0.0`. The version bump intentionally invalidates active v1 cache
entries, so a reviewed re-ingest can exercise the updated parser contract.

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

For one valid source, the pipeline first checks every emitted statement/source
row key in memory, then opens one SQLite savepoint. It creates a `validated`
run; resolves identities; replaces the source's old *derived* run only inside
that uncommitted savepoint; writes every statement, evidence row, normalized
delta, scope, and child row under the new run; records a deterministic content
count/hash; switches the source pointer; and commits. SQLite's v6 global
statement/evidence identities mean the old derived rows are deleted just before
new child writes rather than held side-by-side, but no reader can observe that
intermediate state. Any exception rolls back to the prior committed source.

The result is source-wide replacement rather than statement-by-statement
replacement:

- statements omitted by a newer successful parse are removed with the old run;
- repeated forced ingest of the same resolved output keeps active counts and
  content hash stable; and
- a parser/validation/write failure cannot partially fan out into active rows.

`ingestion_runs` retains failed attempts for audit. Successful replaced runs are
derived output and are removed with their source children; active run content
hash/counts are the authoritative current-extraction record.

## Identity resolution

Resolution runs inside the same source savepoint, after parser validation and
before persistence. It preserves the parser's printed identity and applies only
these deterministic steps:

1. retain an explicit printed ticker or complete option contract;
2. match an exact reviewed user alias or resolved reviewed fund lookup;
3. match one unambiguous exact identity in the same statement's holdings; or
4. retain an `unresolved_printed_identity` with zero confidence.

It does not use the broad free-form name-to-ticker repair map. Transaction rows
store the selected method/confidence and a resolution evidence link when a
source span is available; instrument rows retain their identity provenance.
`ledger ingest repair-symbols` remains a legacy/manual maintenance command for
old derived data, but normal ingest no longer invokes it.

## Post-processing

After a scan, `reconcile_after_ingest()` pairs unambiguous transfers and
recreates position/transaction attribution links for complete scopes, then
rebuilds generated position, cash, and statement-total results. The
reconciliation rebuild replaces only its `recon:v1:*` derived rows; it never
changes a source transaction, reported checkpoint, or balance. A limited scan
still finishes this active-output maintenance rather than returning mid-command.

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

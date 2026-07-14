# Ingestion

This page documents the current PDF-to-SQLite path and its known activation
semantics. Institution parsing details are under `spec/parsers/`.

## Inputs and extraction

`ledger ingest run` walks only `*.pdf` files directly below each directory in
`STATEMENTS_DIR`. Folder names map to institution codes in `config.INSTITUTIONS`;
unknown folders use their literal name as the code.

`pdf_text.extract_pdf()` reads all pages with `pdfplumber`, falls back to
`pypdf` only when the first result is empty, fingerprints the file, and marks
fewer than 20 extracted characters as image-only. OCR is not implemented.

PDFs are immutable inputs. Text dumps under `<DATA_DIR>/text_dumps/` and logs
are derived artifacts.

## Current flow

```text
discover path
  -> hash and contract-aware cache check (unless --force)
  -> extract text
  -> skip image-only / fail unclaimed
  -> first registered parser whose can_handle() returns true
  -> parser.parse(PdfText)
  -> validate the complete ParseResult
  -> conservatively resolve printed identities
  -> stage one source in a SQLite savepoint
  -> atomically activate the fully written source run
  -> pair transfers and rebuild position/transaction links
  -> regenerate derived ingestion audit indexes
```

The registered parsers are CIBC, HSBC, RBC, and TD, all currently reporting
version `1.0.0`.

## Status and cache behavior

`source_files.parse_status` is a compatibility summary of the active
extraction. It is `pending`, `ok`, `partial`, `failed`, or `skipped`. A failed
or skipped attempt is recorded in `ingestion_runs`; if a source already has an
active extraction, its active metadata and pointer remain unchanged.

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
recreates position/transaction attribution links for the active output. It
does not yet calculate cash/position/statement residuals. A limited scan still
finishes this active-output maintenance rather than returning mid-command.

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

The 2026-07-12 Phase 1 runs completed over all 324 stored text dumps and all 338
source PDFs. The PDF run emitted 617 in-memory statements and reported 178
duplicate statement keys, 270 unbalanced calculable cash checks, 214 incomplete
cash checks, and 533 unbalanced position intervals out of 5,941. No source was
unclaimed and no parser crashed. These are a defect baseline, not passing
reconciliation results.

## Remaining work

The Phase 3 source activation boundary is implemented. Parser v1 still emits
known duplicate/missing/ambiguous broker data, and the current reconciliation
command remains link attribution only. See the plan for layout-aware parser
work, residual computation, shared holdings reconstruction, and the shadow
rebuild/cutover.

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
  -> hash (unless --force)
  -> extract text
  -> skip image-only / fail unclaimed
  -> first registered parser whose can_handle() returns true
  -> parser.parse(PdfText)
  -> upsert source metadata
  -> write each emitted statement immediately
  -> repair symbols after the whole scan
  -> pair transfers and rebuild position/transaction links
```

The registered parsers are CIBC, HSBC, RBC, and TD, all currently reporting
version `1.0.0`.

## Status and cache behavior

`source_files.parse_status` is `pending`, `ok`, `partial`, `failed`, or
`skipped`. A source is skipped on a later normal run when path and SHA-256 are
unchanged and prior status is `ok`, `partial`, or `skipped`.

The cache does **not** include parser version, schema version, resolver version,
or output hash. Changing parser code without `--force` can therefore leave old
rows active.

## Persistence behavior

For each emitted `ParsedStatement`, the writer upserts institution/account and
the statement key `(source_file_id, account_id, period_end)`. It then deletes
that statement's transactions, positions, cash, annual performance rows, and
same-source/account quarantine before inserting replacements.

This makes one statement write repeatable in isolation, but it does not make a
source-file parse atomic:

- output is not validated as a whole before writes;
- repeated statement keys in one `ParseResult` overwrite earlier outputs;
- statements emitted by an older parse but omitted by a newer parse survive;
- a failed extraction/parse updates source status but does not activate a new
  coherent source version; and
- there is no persisted ingestion-attempt record or active-run pointer.

SQLite session commit/rollback covers the outer command, but the semantic unit
is still the incremental scan rather than a staged source activation.

## Post-processing

After a full scan, `repair_symbols()` performs conservative name/holding/tax
and reviewed-fund repairs. `reconcile_after_ingest()` then pairs unambiguous
transfers and recreates position/transaction attribution links. An early
return caused by `--limit` occurs before these post-processing calls.

## Logs

- standard logs are configured under `logs/`;
- image-only relpaths append to `logs/skipped_pdfs.log`;
- quarantine items append to `logs/quarantine.jsonl`;
- market commands append to `logs/market_scrape.jsonl`.

The text/JSONL files are append-only event records. They are not exact mirrors
of active database rows and may contain duplicates across runs.

## Required parser boundary

The current pipeline trusts parser dataclasses and performs no contract
validation before persistence. The required semantics and current violations
are documented in [PARSER-CONTRACT.md](PARSER-CONTRACT.md).

## Target activation model (not implemented)

The approved refactor is `discover -> layout extract -> select -> parse ->
validate -> resolve -> stage -> atomically activate -> reconcile -> audit`.
A failed attempt must preserve the last successful active extraction, and the
cache key must include source/parser/schema/resolver versions. See the plan for
the full acceptance gates.

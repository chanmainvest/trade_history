# Offline prompt: draft an institution parser

> **Status:** manual/offline review aid. There is no HTTP upload,
> draft-parser, or LLM-provider workflow in the current application. Nothing
> generated from this prompt is installed or executed automatically.

Use this only after obtaining a redacted text/layout sample without modifying
the source PDF. Never send private statement text, account numbers, names, or
balances to an external provider without the user's explicit authorization.

## Inputs to prepare

- redacted, page-preserving text or word-coordinate fixture;
- `src/ledger/parsers/types.py`;
- `src/ledger/parsers/registry.py`;
- [parser contract](../spec/PARSER-CONTRACT.md);
- the relevant existing parser and institution spec, if extending a known bank;
- exact SQLite DDL only when persistence context is needed.

## Prompt template

```text
You are drafting a deterministic Python parser for a Canadian retail broker
statement. Produce reviewable code and tests; do not write to a database, call
the network, or invent missing values.

Implement the registered Parser protocol:
- NAME and VERSION class attributes;
- can_handle(folder_name, first_page_text) -> bool;
- parse(pdf: PdfText) -> ParseResult.

Use only dataclasses and TxnType literals from parsers/types.py. ParseResult may
contain several ParsedStatement values because one PDF can contain several
accounts or periods. Every emitted statement identity must be unique within the
source.

Requirements:
- preserve native currency;
- preserve printed descriptions and source evidence;
- quarantine uncertain rows with raw text and a precise reason;
- never convert a missing/invalid number to zero;
- retain full option identity;
- split periods before accounts/currency sections;
- do not mark a holdings/cash section complete without source evidence;
- do not create a new parser if an existing institution parser should be
  extended.

Produce:
1. the proposed parser diff;
2. redacted/synthetic fixtures under tests/fixtures/<name>/ for each layout;
3. tests for period/account splitting, signs, native currency, positions, cash,
   options, quarantine, and duplicate statement-key rejection;
4. a short list of ambiguous source rows requiring human review.
```

## Review and installation

Before installing a draft:

1. Compare every expected row against the redacted fixture and a local source
   PDF spot-check.
2. Update the existing institution spec or add a focused new one.
3. Register the parser and bump its version.
4. Run contract/fixture tests and the read-only corpus audit.
5. Run `uv run pytest -q`, `uv run ruff check src tests`, and the frontend build
   if any visible behavior changed.
6. Rebuild only a shadow ledger until collision/reconciliation comparison gates
   pass.

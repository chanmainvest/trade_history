# Specification index

This directory is the on-demand context library for Trade History. Read this
page first, then open only the owner document for the subsystem being changed.

## Authority and status

The documents describe the currently implemented system unless a section is
explicitly labeled **Target**. When sources disagree, use this order:

1. executable schema and code;
2. tests that exercise the behavior;
3. the focused owner specification;
4. `README.md` and the user guide;
5. generated `docs/index.html`.

`src/ledger/db/schema.sql` is the exact SQLite DDL. The `DDL` string in
`src/ledger/db/duckdb_store.py` is the exact DuckDB DDL. The implementation
plan is not evidence that a target behavior already exists.

## Context router

| Question or change | Load |
|---|---|
| What works, what is measured, what is broken? | [CURRENT-STATE.md](CURRENT-STATE.md) |
| Where are the system boundaries and packages? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What is persisted? | [DATA-MODEL.md](DATA-MODEL.md) |
| How does a PDF become ledger rows? | [INGESTION.md](INGESTION.md) |
| What must a parser emit? | [PARSER-CONTRACT.md](PARSER-CONTRACT.md) |
| How are movements, checkpoints, and views related? | [RECONCILIATION.md](RECONCILIATION.md) |
| How are old/new ticker symbols linked over time? | [DATA-MODEL.md](DATA-MODEL.md), [PARSER-CONTRACT.md](PARSER-CONTRACT.md), [RECONCILIATION.md](RECONCILIATION.md) |
| Which HTTP routes and tabs exist? | [API-UI.md](API-UI.md) |
| How are profiles, commands, servers, and releases run? | [OPERATIONS.md](OPERATIONS.md) |
| How does a person use the app? | [USER-GUIDE.md](USER-GUIDE.md) |
| Which lessons apply to several parsers? | [EXTRACTION-CORNER-CASES.md](EXTRACTION-CORNER-CASES.md) |

Institution-specific extraction context:

- [parsers/CIBC.md](parsers/CIBC.md)
- [parsers/HSBC.md](parsers/HSBC.md)
- [parsers/RBC.md](parsers/RBC.md)
- [parsers/TD.md](parsers/TD.md)

## Fact ownership

Keep each contract in one place. Architecture owns the system map, data model
owns persistence, ingestion owns activation semantics, parser contract owns
emitted types and evidence, reconciliation owns formulas, API/UI owns routes
and consumers, and operations owns commands. Other documents should link to
the owner rather than copy it.

When implementation changes, update the owner spec in the same change. Update
`CURRENT-STATE.md` only from a new measured audit, and regenerate
`docs/index.html` after any source-spec change.

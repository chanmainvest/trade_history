# AGENTS.md — Trade History coding-agent guide

Keep this file small. It contains stable rules and routes detailed context to
the focused specifications under `spec/`.

## Non-negotiable rules

1. Statement PDFs are read-only inputs. Never move, rename, edit, or delete them.
2. Quarantine uncertainty; never invent a number, symbol, sign, or balancing row.
3. Store statement amounts in native currency. FX conversion is presentation-only.
4. Complete broker snapshots are checkpoints; transactions are the audit trail.
   Do not treat a partial extraction as a complete snapshot.
5. Preserve user changes in a dirty worktree. Do not revert unrelated edits.
6. Keep parsed data traceable to its source. Parser changes require a defensible
   source fixture or a spot-check against the cited PDF.
7. Documentation is part of the change. Update the owning spec with the code.

## Load context on demand

Start at [spec/INDEX.md](spec/INDEX.md), then load only the files needed:

| Work area | Required context |
|---|---|
| Current behavior and known defects | `spec/CURRENT-STATE.md` |
| System boundaries/package map | `spec/ARCHITECTURE.md` |
| SQLite or DuckDB | `spec/DATA-MODEL.md` + canonical DDL |
| PDF extraction or persistence | `spec/INGESTION.md` |
| Parser types or validation | `spec/PARSER-CONTRACT.md` |
| CIBC, HSBC, RBC, or TD | `spec/parsers/<INSTITUTION>.md` |
| Reconciliation or holdings-at-date | `spec/RECONCILIATION.md` |
| FastAPI or React | `spec/API-UI.md` |
| Profiles, CLI, Docker, servers, release | `spec/OPERATIONS.md` |
| Human-facing behavior | `spec/USER-GUIDE.md` |
| Cross-parser lessons | `spec/EXTRACTION-CORNER-CASES.md` |

The refactor sequence and acceptance gates live in
`plan/EXTRACTION_RECONCILIATION_REFACTOR.md`. A plan describes intended work,
not implemented behavior.

## Repository and tools

- Python 3.12+, FastAPI, Click, SQLite, DuckDB, `pdfplumber`/`pypdf`.
- React 18, Vite, TypeScript, Plotly, React Query, React Router.
- Use `uv run ...` for every Python command; never use bare `python` or `pip`.
- Use `npm` in `frontend/`.
- Prefer `rg`/`rg --files` for repository searches.
- Edit files with patch-based changes and preserve unrelated local modifications.

Set a profile before Python imports `ledger.config`:

```powershell
$env:LEDGER_PROFILE = "example"  # example_data/
$env:LEDGER_PROFILE = "real"     # Statements/ + data/ (default)
```

## Required validation

Run checks proportionate to the change; before completing a structural change,
run all three:

```powershell
uv run pytest -q
uv run ruff check src tests
cd frontend; npm run build
```

For documentation changes, also run:

```powershell
uv run python scripts/build_docs.py
uv run python scripts/build_docs.py --check
```

Before finishing, re-read the changed code, its owning spec, `README.md` when
quick-start/layout changed, and `spec/USER-GUIDE.md` when behavior is visible.
Generated `docs/index.html` must match the source specs.

## Frontend conventions

- Server data uses React Query; local UI state uses `useState`.
- Preferences flow through `usePortfolio()` and `/config`, not local storage.
- Add user-visible strings to `frontend/src/i18n.tsx` and call `t(...)`.
- Theme colors use CSS variables and `plotlyTheme()`.

## Safety boundary

The web UI may update preferences in `data/config.json`, but statement ingest,
symbol repair, reconciliation, and ledger-database mutation are CLI-only.
Never expose secrets or private statement contents in logs, fixtures, or commits.

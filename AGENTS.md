# Trade History Agent Notes

## Tech Stack

- Backend framework: Python `FastAPI`.
- Frontend framework: React + Vite + TypeScript.
- Datastores:
  - SQLite for trading/activity data.
  - DuckDB for market/fx price datasets.
- Package/runtime tooling:
  - Python dependency and run tool: `uv` (required).
  - Frontend package manager: `npm`.
  - Container runtime: Docker / Docker Compose.

## Tooling Rules

- Always use `uv` for Python commands and workflows:
  - use `uv sync`, `uv run ...`, and `uv run python ...`.
  - do not use direct `pip`, `python`, or `pytest` commands unless explicitly requested.
- Keep Python invocations reproducible from the repo root.

## Security Scan Rules

- Before every push, run a security/PII scan across source and config files.
- Minimum scan scope:
  - `src/`, `scripts/`, `tests/`, `frontend/src/`, `Dockerfile`, `docker-compose.yml`, `README.md`, `AGENTS.md`, `.env.example`.
- Exclude local data and statements:
  - `data/`, `Statements/`, `frontend/node_modules/`, `frontend/dist/`, `.venv/`, `.uv-cache/`.
- Verify no committed personal identifiers, account numbers, addresses, secrets, private keys, or access tokens.
- If sensitive values are found:
  - replace with neutral placeholders,
  - re-run scans,
  - then commit/push.

## Parsing Rules

- Treat account-ID extraction as high-risk.
- Accept explicit account tokens only when they contain digits and normalize to at least 6 alphanumeric characters (excluding `-`).
- Reject short numeric tokens that often appear in addresses (for example `1234` in street addresses).
- Keep unresolved/ambiguous rows in `quarantine_transactions` instead of forcing partial parses.
- Prefer data-driven parser corrections:
  - Use `scripts/gemini_extract_samples.sh` to generate sample line-level overrides with `gemini-2.5-flash`.
  - Store normalized override files under `data/gemini_overrides/<Institution>/<StatementStem>.json`.
  - Re-run `uv run trade-history ingest statements --force` after parser/override changes.

## Parser Format Conventions

- Each institution has distinct PDF layout quirks that parsers must respect:
  - **RBC**: Full month names (`JUNE 28`, `MAY 31, 2024`), trailing-hyphen negatives (`32,870.00-`), action keyword `BOUGHT`.
  - **TD**: Abbreviated month + day (`Jun 18`), option format `CALL-100 CNQ'25 JA@50` or `PUT -100 ENB'24 SP@44`.
  - **HSBC**: Compact option format `PUT-100TLT'2616JA@75` (no spaces between multiplier and root).
  - **CIBC Invest Direct / TSFA**: `DISTRIBUTION` as dividend action, option expiry may be `MM/DD/YY`.
  - **CIBC Imperial Service**: Account ID fallback searches filename for `\d{3}[-]?\d{5}` pattern; must never generate time-dependent placeholders.
- `parse_money()` must handle both standard negatives (`-1,234.56`) and trailing-hyphen format (`1,234.56-`).
- `DATE_RE` must match both abbreviated (`Jan`, `Feb`) and full (`January`, `February`) month names.
- Dividend events must preserve instrument context; only interest/fee events should clear it.

## Analytics Rules

- `asset_values()` must compute market value as `quantity * price * COALESCE(multiplier, 1)` for correct option valuation.
- Reconciliation formula: `derived_closing = opening + net_cash - fees`. Never double-count fees.
- Currency P&L: convert market value to instrument's native currency before subtracting native cost basis.
- Never mix CAD and USD in unrealized P&L calculations.

## Instrument Modeling

- Keep options and stocks first-class in the same schema:
  - `instruments.asset_type` must distinguish `equity` and `option`.
  - Option contracts keep `option_root`, `strike`, `expiry`, `put_call`, `multiplier`.
- Never flatten option positions into stock positions in analytics or UI.

## Asset Value Tab Contract

- `asset_values(group_by='account')` must return account grouping with institution context:
  - `group_key = "{institution} | {account_id}"`.
- Position rows must include `asset_type`.
- Frontend must show separate sections for:
  - `Stocks` (`asset_type != option`)
  - `Options` (`asset_type == option`)

## Database Schema Rules

- DuckDB tables must have `UNIQUE` constraints on their natural keys to prevent silent duplicates on re-ingestion.
- SQLite foreign keys that reference `source_files` or parent events must use `ON DELETE CASCADE` so re-ingesting a file safely replaces old rows.
- Index `lot_closures` on `close_event_id` and `(account_id, instrument_id)` for P&L query performance.

## Required Validation After Parser/UI Changes

- Run:
  - `uv run pytest -q`
  - `uv run ruff check src tests`
  - `npm run build` (in `frontend/`)
- Re-ingest statements and verify:
  - no false account like `1234` in `accounts`
  - both `equity` and `option` rows exist in `position_state` joined to `instruments`
  - `lot_closures` CASCADE deletes work when re-ingesting (no orphaned rows)
  - DuckDB tables reject duplicate natural key inserts (OR IGNORE semantics)

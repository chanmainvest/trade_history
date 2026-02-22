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

## Required Validation After Parser/UI Changes

- Run:
  - `uv run pytest -q`
  - `uv run ruff check src tests`
  - `npm run build` (in `frontend/`)
- Re-ingest statements and verify:
  - no false account like `1234` in `accounts`
  - both `equity` and `option` rows exist in `position_state` joined to `instruments`

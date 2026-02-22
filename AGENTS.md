# Trade History Agent Notes

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

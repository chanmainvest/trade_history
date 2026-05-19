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

## PDF Extraction

- PDF text extraction uses **docling** as the primary engine (`convert_pdf_via_docling()` in `extractors/utils.py`).
- Docling's markdown output is stripped via `strip_markdown()` to produce pdfplumber-compatible plain text for existing regex parsers.
- Docling's `export_to_dict()` JSON is stored in two places:
  - `statement_registry.docling_json` column in SQLite.
  - `data/docling_json/<parent_dir>/<stem>.json` files on disk.
- On `--force` re-ingest, the pipeline pre-loads cached JSON from the database via `cache_docling_json()` so docling does not re-run. Text is reconstructed from stored JSON via `text_from_docling_dict()`.
- **pdfplumber** is retained for two purposes:
  - Fast `can_handle()` resolution via `get_first_page_text()` (LRU cached).
  - Fallback if docling is unavailable or fails.
- Image-based PDFs (empty pdfplumber text) are handled by docling's built-in OCR capabilities.

## Parsing Rules

- Treat account-ID extraction as high-risk.
- Accept explicit account tokens only when they contain digits and normalize to at least 6 alphanumeric characters (excluding `-`).
- Reject short numeric tokens that often appear in addresses (for example `1234` in street addresses).
- Keep unresolved/ambiguous rows in `quarantine_transactions` instead of forcing partial parses.
- Prefer data-driven parser corrections:
  - Use `scripts/gemini_extract_samples.sh` to generate sample line-level overrides with `gemini-3-pro`.
  - Store normalized override files under `data/gemini_overrides/<Institution>/<StatementStem>.json`.
  - Re-run `uv run trade-history ingest statements --force` after parser/override changes.

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

## Statements Tab

- Split-view: PDF with docling bounding box overlays (left) and structured JSON viewer (right).
- Bidirectional highlighting: click a box on the PDF → highlight JSON entry; click JSON entry → highlight box.
- Trade rows in the Trades tab link to the corresponding statement (via `statement_id`).
- Coordinate transforms: docling uses both BOTTOMLEFT and TOPLEFT origins depending on element type.

## Frontend Global Settings

- `GlobalSettings` interface: `{ currency: 'CAD' | 'USD'; language: Language }`.
- Currency and language toggles are in the header bar (no separate settings page).
- No privacy/blur mode — amounts are always shown.

## Required Validation After Parser/UI Changes

- Run:
  - `uv run pytest -q`
  - `uv run ruff check src tests`
  - `npm run build` (in `frontend/`)
- Re-ingest statements and verify:
  - no false account like `1234` in `accounts`
  - both `equity` and `option` rows exist in `position_state` joined to `instruments`

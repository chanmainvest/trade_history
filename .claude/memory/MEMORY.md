# Trade History Project — Session Notes

## Project state
Full skeleton implemented and passing: 39 pytest tests, ruff clean, frontend builds.

## Key gotchas found

### pytest tmp_path on Windows
`tmp_path` fixture fails with `PermissionError: [WinError 5]` on this machine.
**Fix:** Use `tempfile.TemporaryDirectory()` with `yield` in fixtures instead.

### transactions.source_file is NOT NULL
The schema requires `source_file` in every INSERT into `transactions`.
Always include it in test inserts, e.g.: `..., source_file) VALUES (..., 'test.pdf')`.

### uv dev-dependencies deprecation warning
`[tool.uv.dev-dependencies]` is deprecated; should migrate to `[dependency-groups]`.
Non-breaking for now — ignore.

### DuckDB FROM df pattern
`df = pd.DataFrame(rows)` before `conn.execute("... FROM df")` looks unused to ruff.
Suppress with `# noqa: F841` — DuckDB reads the local variable by name in SQL.

## Architecture summary
- SQLite: trades/accounts/positions (canonical)
- DuckDB: OHLCV prices + FX rates (market data)
- Extractors: one module per institution under `src/trade_history/extractors/<inst>/`
- Registry: `@ExtractorRegistry.register` decorator; imports triggered by `extractors/__init__.py`
- Ingest: `uv run trade-history ingest statements --statements-dir <path>`
- API: FastAPI on :8000; frontend proxied via Vite dev server
- Gemini overrides: `data/gemini_overrides/<Institution>/<StatementStem>.json`

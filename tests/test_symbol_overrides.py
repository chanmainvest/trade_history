from __future__ import annotations

from pathlib import Path
import uuid

from trade_history.config import settings
from trade_history.db.sqlite import get_connection, init_db
from trade_history.ingest.market import resolve_market_symbol
from trade_history.services.symbols import delete_symbol_override, list_symbol_overrides, upsert_symbol_override


def test_resolve_market_symbol_override_precedence() -> None:
    overrides = {"APPLE": "AAPL"}
    assert resolve_market_symbol("APPLE", overrides=overrides) == "AAPL"
    assert resolve_market_symbol("MICROSOFT", overrides={}) == "MSFT"
    assert resolve_market_symbol("XYZUNKNOWN", overrides={}) == "XYZUNKNOWN"


def test_upsert_and_delete_symbol_override() -> None:
    original_db = settings.sqlite_path
    temp_db = Path("data") / f"test-symbol-overrides-{uuid.uuid4().hex}.sqlite"
    settings.sqlite_path = temp_db
    try:
        init_db()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO instruments(symbol_raw, symbol_norm, asset_type, sector)
                VALUES ('APPLE', 'APPLE', 'equity', NULL)
                """
            )
            conn.commit()

        upsert_symbol_override(
            symbol_norm="APPLE",
            market_symbol="AAPL",
            sector_override="Technology",
            notes="manual mapping",
            is_active=True,
        )
        overrides = list_symbol_overrides()["items"]
        assert len(overrides) == 1
        assert overrides[0]["symbol_norm"] == "APPLE"
        assert overrides[0]["market_symbol"] == "AAPL"
        assert overrides[0]["is_active"] == 1

        delete_symbol_override("APPLE")
        overrides_after = list_symbol_overrides()["items"]
        assert overrides_after[0]["is_active"] == 0
    finally:
        settings.sqlite_path = original_db

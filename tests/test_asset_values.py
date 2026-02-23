from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trade_history.config import settings
from trade_history.db.sqlite import get_connection, init_db
from trade_history.services.analytics import asset_values


def test_asset_values_include_asset_type_and_account_group_institution_prefix() -> None:
    original_sqlite_path = settings.sqlite_path
    test_path = Path("data/test_asset_values.sqlite")
    if test_path.exists():
        try:
            test_path.unlink()
        except PermissionError:
            pass
    settings.sqlite_path = test_path
    try:
        init_db()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO accounts(account_id, institution, account_name)
                VALUES ('A1', 'Broker A', 'Main Account')
                """
            )
            conn.execute(
                """
                INSERT INTO instruments(symbol_raw, symbol_norm, asset_type)
                VALUES ('AAPL', 'AAPL', 'equity')
                """
            )
            conn.execute(
                """
                INSERT INTO instruments(symbol_raw, symbol_norm, asset_type, option_root, strike, expiry, put_call, multiplier)
                VALUES ('AAPL C 200', 'AAPL_OPT', 'option', 'AAPL', 200, '2025-03-21', 'C', 100)
                """
            )
            conn.execute(
                """
                INSERT INTO position_state(
                  account_id, instrument_id, currency, quantity, cost_total_native, avg_cost_native,
                  as_of_event_id, as_of_trade_date
                ) VALUES
                  ('A1', 1, 'USD', 10, 1000, 100, 1, '2025-01-31'),
                  ('A1', 2, 'USD', 2, 300, 150, 1, '2025-01-31')
                """
            )
            conn.commit()

        with patch(
            "trade_history.services.analytics._latest_prices",
            return_value={"AAPL": (150.0, "USD"), "AAPL_OPT": (4.0, "USD")},
        ):
            result = asset_values(display_currency="USD", group_by="account")

        assert len(result["items"]) == 1
        group = result["items"][0]
        assert group["group_key"] == "Broker A | A1"
        position_types = {item["symbol"]: item["asset_type"] for item in group["positions"]}
        assert position_types["AAPL"] == "equity"
        assert position_types["AAPL_OPT"] == "option"
        option_row = next(item for item in group["positions"] if item["symbol"] == "AAPL_OPT")
        assert option_row["option_root"] == "AAPL"
        assert option_row["strike"] == 200.0
        assert option_row["expiry"] == "2025-03-21"
        assert option_row["put_call"] == "C"
    finally:
        settings.sqlite_path = original_sqlite_path
        if test_path.exists():
            try:
                test_path.unlink()
            except PermissionError:
                pass

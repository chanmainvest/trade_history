from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from trade_history.config import settings
from trade_history.parsers.base import ParsedEvent
from trade_history.parsers.gemini_overrides import apply_event_override, load_event_overrides


def test_load_and_apply_gemini_override_for_option_symbol() -> None:
    original_root = settings.gemini_overrides_root
    test_root = Path("data/test_gemini_overrides")
    settings.gemini_overrides_root = test_root
    try:
        override_file = settings.gemini_overrides_root / "TD Webbroker" / "Statement_58MRB0_2024-12.json"
        override_file.parent.mkdir(parents=True, exist_ok=True)
        override_file.write_text(
            json.dumps(
                {
                    "transactions": [
                        {
                            "source_line_ref": "p1:l7",
                            "event_type": "trade",
                            "side": "SELL",
                            "symbol_norm": "AAPL",
                            "asset_type": "option",
                            "option_root": "AAPL",
                            "put_call": "C",
                            "strike": 225,
                            "expiry": "2025-01-17",
                            "quantity": 10,
                            "price": 20.45,
                            "gross_amount": 20450.0,
                            "currency": "USD",
                            "commission": 1.4,
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        overrides = load_event_overrides(Path("Statements/TD Webbroker/Statement_58MRB0_2024-12.pdf"))
        assert "p1:l7" in overrides.events_by_line_ref
        assert overrides.source_path == override_file

        event = ParsedEvent(
            account_id="58MRB0",
            trade_date=date(2024, 12, 9),
            settle_date=None,
            event_type="trade",
            side="SELL",
            quantity=10,
            price=20.45,
            gross_amount=20450.0,
            commission=0.0,
            fees=0.0,
            currency="USD",
            instrument=None,
            source_line_ref="p1:l7",
        )
        updated = apply_event_override(event, overrides.events_by_line_ref["p1:l7"])
        assert updated.instrument is not None
        assert updated.instrument.symbol_norm == "AAPL"
        assert updated.instrument.asset_type == "option"
        assert updated.instrument.option_root == "AAPL"
        assert updated.instrument.put_call == "C"
        assert updated.instrument.strike == 225.0
        assert updated.instrument.expiry == date(2025, 1, 17)
        assert updated.commission == 1.4
    finally:
        settings.gemini_overrides_root = original_root
        if override_file.exists():
            override_file.unlink()
        td_folder = test_root / "TD Webbroker"
        if td_folder.exists():
            try:
                td_folder.rmdir()
            except OSError:
                pass
        if test_root.exists():
            try:
                test_root.rmdir()
            except OSError:
                pass

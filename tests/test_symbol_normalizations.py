from __future__ import annotations

import json
import sqlite3

import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest.symbol_normalizations import (
    apply_symbol_normalizations,
    load_symbol_normalizations,
)


def _seed_ledger(db_path: sqlite3.Connection | str) -> None:
    """Minimal FK-valid account/statement skeleton for remap targets."""
    if isinstance(db_path, sqlite3.Connection):
        conn = db_path
    else:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO institutions(institution_id, code, display_name)"
        " VALUES (1, 'TST', 'Test Broker')"
    )
    conn.execute(
        "INSERT INTO accounts(account_id, institution_id, account_number,"
        " base_currency) VALUES (1, 1, 'A1', 'CAD')"
    )
    conn.execute(
        "INSERT INTO source_files(source_file_id, relpath) VALUES (1, 'x.pdf')"
    )
    conn.execute(
        "INSERT INTO ingestion_runs(ingestion_run_id, source_file_id,"
        " contract_version, schema_version, status, started_at)"
        " VALUES (1, 1, 'test', 1, 'active', '2026-09-06T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO statements(statement_id, account_id, source_file_id,"
        " ingestion_run_id, statement_key, period_start, period_end)"
        " VALUES (1, 1, 1, 1, 'k1', '2026-05-01', '2026-05-31')"
    )
    if not isinstance(db_path, sqlite3.Connection):
        conn.commit()
        conn.close()


ENTRIES = [
    {
        "printed_symbol": "5SOXS",
        "canonical_symbol": "SOXS1",
        "asset_type": "option",
        "currency": "USD",
        "option_expiry": "2027-01-15",
        "option_strike": 34.0,
        "option_type": "CALL",
        "resolution_method": "reviewed_statement_reprint",
    },
    {
        "printed_symbol": "98TRI+$",
        "canonical_symbol": "TRI",
        "asset_type": "option",
        "currency": "CAD",
        "option_expiry": "2026-06-19",
        "option_strike": 120.0,
        "option_type": "CALL",
    },
]


def _upsert_printed(conn: sqlite3.Connection, symbol: str, currency: str,
                    expiry: str, strike: float) -> int:
    return sqlite_db.upsert_instrument(
        conn, asset_type="option", symbol=symbol, currency=currency,
        option_root=symbol, option_expiry=expiry, option_strike=strike,
        option_type="CALL",
    )


def _write_json(tmp_path, data):
    path = tmp_path / "norms.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _load(tmp_path, data):
    if isinstance(data, list):
        data = {"symbol_normalizations": data}
    return load_symbol_normalizations(str(_write_json(tmp_path, data)))


def test_upsert_hook_resolves_printed_symbols_after_apply(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _seed_ledger(conn)
        printed_soxs = _upsert_printed(conn, "5SOXS", "USD", "2027-01-15", 34.0)
        printed_tri = _upsert_printed(conn, "98TRI+$", "CAD", "2026-06-19", 120.0)

        stats = apply_symbol_normalizations(conn, ENTRIES)
        assert stats["rules_inserted"] == 2
        assert stats["instruments_merged"] == 2

        # Re-extraction resolves the printed symbols to canonical ids.
        resolved_soxs = _upsert_printed(conn, "5SOXS", "USD", "2027-01-15", 34.0)
        resolved_tri = _upsert_printed(conn, "98TRI+$", "CAD", "2026-06-19", 120.0)
        canonical_soxs = conn.execute(
            "SELECT instrument_id FROM instruments WHERE symbol = 'SOXS1'"
            " AND option_expiry = '2027-01-15' AND option_strike = 34.0"
        ).fetchone()[0]
        canonical_tri = conn.execute(
            "SELECT instrument_id FROM instruments WHERE symbol = 'TRI'"
            " AND asset_type = 'option' AND option_expiry = '2026-06-19'"
        ).fetchone()[0]
        assert resolved_soxs == canonical_soxs != printed_soxs
        assert resolved_tri == canonical_tri != printed_tri

        # The printed symbols stay recorded with their canonical targets.
        rules = {
            row["printed_symbol"]: row["canonical_instrument_id"]
            for row in conn.execute(
                "SELECT printed_symbol, canonical_instrument_id"
                " FROM instrument_symbol_normalizations"
            )
        }
        assert rules == {
            "5SOXS": canonical_soxs,
            "98TRI+$": canonical_tri,
        }


def test_upsert_hook_leaves_other_identities_untouched(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _seed_ledger(conn)
        apply_symbol_normalizations(conn, ENTRIES)
        # A different contract generation under the same adjusted root is
        # NOT covered by the rule (option identity includes expiry/strike).
        other = sqlite_db.upsert_instrument(
            conn, asset_type="option", symbol="5SOXS", currency="USD",
            option_root="5SOXS", option_expiry="2028-01-21",
            option_strike=34.0, option_type="CALL",
        )
        symbol = conn.execute(
            "SELECT symbol FROM instruments WHERE instrument_id = ?", (other,)
        ).fetchone()[0]
        assert symbol == "5SOXS"
        # Equity symbols never match an option rule.
        equity = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="98TRI+$", currency="CAD",
        )
        assert conn.execute(
            "SELECT symbol FROM instruments WHERE instrument_id = ?", (equity,)
        ).fetchone()[0] == "98TRI+$"


def test_apply_merges_existing_ledger_rows(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _seed_ledger(conn)
        printed_tri = _upsert_printed(conn, "98TRI+$", "CAD", "2026-06-19", 120.0)
        conn.execute(
            "INSERT INTO transactions(account_id, statement_id, trade_date,"
            " txn_type, instrument_id, quantity, currency)"
            " VALUES (1, 1, '2026-06-22', 'option_expiration', ?, -1, 'CAD')",
            (printed_tri,),
        )
        apply_symbol_normalizations(conn, ENTRIES[:1])
        stats = apply_symbol_normalizations(conn, [ENTRIES[1]])
        assert stats["instruments_merged"] == 1
        assert stats["remapped"]["transactions"] == 1
        canonical_tri = conn.execute(
            "SELECT instrument_id FROM instruments WHERE symbol = 'TRI'"
            " AND asset_type = 'option' AND option_expiry = '2026-06-19'"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT instrument_id FROM transactions"
        ).fetchone()[0] == canonical_tri

        # Re-apply is idempotent: the rule updates, nothing remaps.
        again = apply_symbol_normalizations(conn, [ENTRIES[1]])
        assert again["rules_updated"] == 1
        assert again["instruments_merged"] == 0
        assert again["remapped"]["transactions"] == 0


def test_apply_entry_validation(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _seed_ledger(conn)

    def apply_entries(data):
        with sqlite_db.session(db_path) as conn:
            return apply_symbol_normalizations(conn, _load(tmp_path, data))

    with pytest.raises(ValueError, match="symbol_normalizations"):
        load_symbol_normalizations(
            str(_write_json(tmp_path, {"something_else": []}))
        )
    with pytest.raises(ValueError, match="must differ"):
        apply_entries([{"printed_symbol": "TRI", "canonical_symbol": "TRI",
                        "asset_type": "equity", "currency": "CAD"}])
    with pytest.raises(ValueError, match="option_expiry"):
        apply_entries([{"printed_symbol": "5SOXS", "canonical_symbol": "SOXS1",
                        "asset_type": "option", "currency": "USD"}])
    with pytest.raises(ValueError, match="effective_date"):
        apply_entries([{"printed_symbol": "5SOXS", "canonical_symbol": "SOXS1",
                        "asset_type": "option", "currency": "USD",
                        "option_expiry": "2027-01-15", "option_strike": 34.0,
                        "option_type": "CALL",
                        "effective_date": "2026-07"}])
    # Case normalization happens at apply time, not in the loader.
    entries = _load(
        tmp_path,
        [{"printed_symbol": "5soxs", "canonical_symbol": "soxs1",
          "asset_type": "option", "currency": "usd",
          "option_expiry": "2027-01-15", "option_strike": 34.0,
          "option_type": "call"}],
    )
    assert entries[0]["currency"] == "usd"
    with sqlite_db.session(db_path) as conn:
        apply_symbol_normalizations(conn, entries)
        row = conn.execute(
            "SELECT printed_symbol, canonical_symbol, currency, option_type"
            " FROM instrument_symbol_normalizations"
        ).fetchone()
        assert tuple(row) == ("5SOXS", "SOXS1", "USD", "CALL")

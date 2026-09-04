import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest.fund_lookup import (
    apply_reviewed_lookups,
    lookup_fund_code,
    lookup_fund_instrument_id,
    normalize_fund_name,
)


def test_normalize_fund_name_keeps_class():
    assert normalize_fund_name("CIBC Monthly Income Fund 175.180 -- -- | CL F REINVESTED DIV") == \
        "CIBC MONTHLY INCOME FUND CLASS F"


def test_lookup_fund_code_queues_pending_then_resolves():
    conn = sqlite_db.connect(":memory:")
    conn.executescript(sqlite_db._SCHEMA)

    match = lookup_fund_code(
        conn,
        fund_name="CIBC Monthly Income Fund",
        currency="CAD",
        institution_code="CIBC_ID",
        sample_description="CIBC MONTHLY INCOME FUND 175.180 -- -- | CL F REINVESTED DIV",
    )
    assert match is None

    row = conn.execute(
        "SELECT status, normalized_name FROM instrument_identifier_lookups"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["normalized_name"] == "CIBC MONTHLY INCOME FUND CLASS F"

    conn.execute(
        "UPDATE instrument_identifier_lookups "
        "SET status = 'resolved', resolved_symbol = 'CIB999', resolved_name = 'CIBC Monthly Income Fund Class F'"
    )
    instrument_id = lookup_fund_instrument_id(
        conn,
        fund_name="CIBC Monthly Income Fund",
        currency="CAD",
        institution_code="CIBC_ID",
        sample_description="CIBC MONTHLY INCOME FUND 175.180 -- -- | CL F REINVESTED DIV",
    )

    instrument = conn.execute(
        "SELECT asset_type, symbol, currency, name FROM instruments WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()
    assert instrument["asset_type"] == "mutual_fund"
    assert instrument["symbol"] == "CIB999"
    assert instrument["currency"] == "CAD"
    assert instrument["name"] == "CIBC Monthly Income Fund Class F"


def _entry(**overrides) -> dict:
    entry = {
        "normalized_name": "CIBC MONTHLY INCOME FUND CLASS F",
        "currency": "CAD",
        "institutions": ["CIBC_ID"],
        "resolved_symbol": "ATL239",
        "resolved_exchange": None,
        "resolved_name": "CIBC Monthly Income Fund - Class F",
        "evidence_url": "https://example.com/fund-facts",
        "notes": "researched identity",
    }
    entry.update(overrides)
    return entry


def _lookup(conn):
    return lookup_fund_code(
        conn,
        fund_name="CIBC Monthly Income Fund Cl F",
        currency="CAD",
        institution_code="CIBC_ID",
        sample_description="CIBC MONTHLY INCOME FUND CL F REINVESTED DIV @ 9.5142",
    )


def test_apply_reviewed_lookups_resolves_pending_row(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        # First ingest queues the printed fund name for review.
        assert _lookup(conn) is None
        pending = conn.execute(
            "SELECT status FROM instrument_identifier_lookups "
            " WHERE normalized_name = 'CIBC MONTHLY INCOME FUND CLASS F'"
        ).fetchall()
        assert [row["status"] for row in pending] == ["pending"]

        # The reviewed record resolves the same normalized name...
        out = apply_reviewed_lookups(conn, [_entry()])
        assert out["entries"] == 1
        assert out["rows_updated"] == 1
        assert out["status_counts"].get("resolved") == 1
        match = _lookup(conn)
        assert match is not None
        assert match.symbol == "ATL239"
        assert match.asset_type == "mutual_fund"

        # ...and the resolved lookup produces a real mutual-fund instrument.
        instrument_id = lookup_fund_instrument_id(
            conn,
            fund_name="CIBC Monthly Income Fund Cl F",
            currency="CAD",
            institution_code="CIBC_ID",
            sample_description="CIBC MONTHLY INCOME FUND CL F REINVESTED DIV @ 9.5142",
        )
        instrument = conn.execute(
            "SELECT symbol, asset_type, name FROM instruments "
            " WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()
        assert instrument["symbol"] == "ATL239"
        assert instrument["asset_type"] == "mutual_fund"

        # Re-applying the same review updates the same row: idempotent.
        again = apply_reviewed_lookups(conn, [_entry()])
        assert again["rows_updated"] == 1
        assert again["rows_inserted"] == 0
        count = conn.execute(
            "SELECT COUNT(*) FROM instrument_identifier_lookups"
        ).fetchone()[0]
        assert count == 1


def test_apply_reviewed_lookups_inserts_missing_and_multi_institution(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        out = apply_reviewed_lookups(
            conn,
            [
                _entry(
                    normalized_name="CIBC DIVIDEND GROWTH FUND",
                    resolved_symbol="ATL486",
                    institutions=["CIBC_IS", "CIBC_ID"],
                )
            ],
        )
        assert out["entries"] == 1
        assert out["rows_inserted"] == 2
        rows = conn.execute(
            "SELECT institution_code, resolved_symbol, status "
            "  FROM instrument_identifier_lookups"
        ).fetchall()
        assert {
            (row["institution_code"], row["resolved_symbol"], row["status"])
            for row in rows
        } == {
            ("CIBC_IS", "ATL486", "resolved"),
            ("CIBC_ID", "ATL486", "resolved"),
        }


def test_apply_reviewed_lookups_rejects_made_up_symbol_without_writing(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        assert _lookup(conn) is None  # queues the pending row

        with pytest.raises(ValueError):
            apply_reviewed_lookups(
                conn, [_entry(resolved_symbol="CIBC_MONTHLY_INCOME")]
            )
        with pytest.raises(ValueError):
            apply_reviewed_lookups(conn, [_entry(resolved_symbol="")])
        with pytest.raises(ValueError):
            apply_reviewed_lookups(conn, [_entry(normalized_name="NOT EVEN A FUND")])

        # Nothing resolved: the pending row stays pending and lookups keep
        # returning None.
        statuses = conn.execute(
            "SELECT status FROM instrument_identifier_lookups"
        ).fetchall()
        assert {row["status"] for row in statuses} == {"pending"}
        assert _lookup(conn) is None

from ledger.db import sqlite as sqlite_db
from ledger.ingest.fund_lookup import lookup_fund_code, lookup_fund_instrument_id, normalize_fund_name


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

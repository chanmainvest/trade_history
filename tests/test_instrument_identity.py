"""Listing catalog, market-symbol, and journal-pair regressions."""
from __future__ import annotations

from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
from ledger.ingest.reconcile import link_transfers
from ledger.ingest.yahoo_resolution import verify_yahoo_identities
from ledger.instrument_catalog import listing_for_symbol, listing_for_text
from ledger.market.scrape import _held_symbols
from ledger.parsers.types import (
    ParsedAccount,
    ParsedInstrument,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
)


def _transaction(name: str, currency: str) -> ParsedTxn:
    return ParsedTxn(
        trade_date="2024-01-05",
        settle_date=None,
        txn_type="buy",
        instrument=ParsedInstrument(
            "equity",
            name[:12],
            currency,
            name=name,
            resolution_method="unresolved_printed_identity",
        ),
        quantity=1,
        price=1,
        gross_amount=1,
        commission=0,
        other_fees=0,
        net_amount=-1,
        currency=currency,
        description=name,
        raw_line=name,
    )


def test_hsbc_compact_names_resolve_to_exchange_listings(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    expected = {
        "BCEINC": ("BCE", "BCE.TO"),
        "BMOMONEYMARKETFUNDETF": ("ZMMK", "ZMMK.TO"),
        "HORIZONS0-3MUSTBETF-A": ("UBIL.U", "UBIL-U.TO"),
        "HORIZONSUSDOLLCURRETF": ("DLR", "DLR.TO"),
        "HORZNHIGHINTSVGSETF-A": ("CASH", "CASH.TO"),
        "ISHARESIBOXX$INVGRCRP": ("LQD", "LQD"),
        "NUTRIENLTD": ("NTR", "NTR.TO"),
        "PURPOSEHIINTSVGFDETF": ("PSA", "PSA.TO"),
        "ROGERSCOMMUNICATION-BNV": ("RCI.B", "RCI-B.TO"),
        "TCENERGYCORP": ("TRP", "TRP.TO"),
        "TELUSCORP": ("T", "T.TO"),
        "VANGUARDINTER-TRMCRPBD": ("VCIT", "VCIT"),
    }
    statement = ParsedStatement(
        account=ParsedAccount("HSBC-1", "Margin"),
        period_start="2024-01-01",
        period_end="2024-01-31",
        transactions=[_transaction(name, "CAD" if ticker.endswith(".TO") else "USD")
                      for name, (_symbol, ticker) in expected.items()],
    )
    # LQD/VCIT are USD despite having no suffix; all other no-suffix cases in
    # this fixture are explicitly overridden by the expected provider symbol.
    for transaction, (_name, (_symbol, ticker)) in zip(
        statement.transactions, expected.items(), strict=True
    ):
        transaction.currency = "USD" if ticker in {"LQD", "VCIT", "UBIL-U.TO"} else "CAD"
        transaction.instrument.currency = transaction.currency
    result = ParseResult("hsbc", "fixture", statements=[statement])

    with sqlite_db.session(db_path) as conn:
        resolve_parse_result(conn, institution_code="HSBC_IDI", result=result)

    actual = {
        transaction.description: (
            transaction.instrument.symbol,
            transaction.instrument.market_symbol,
        )
        for transaction in statement.transactions
        if transaction.instrument is not None
    }
    assert actual == expected
    assert listing_for_text("RCI", "CAD", institution_code="CIBC_ID") == (
        listing_for_symbol("RCI.B", "CAD")
    )


def test_observed_hsbc_truncated_pseudo_tickers_resolve_by_currency():
    expected = {
        ("BCEINC", "CAD"): "BCE",
        ("BOMMONEYMARK", "CAD"): "ZMMK",
        ("HORIZONS0-3M", "USD"): "UBIL.U",
        ("HORIZONSUSDO", "CAD"): "DLR",
        ("HORIZONSUSDO", "USD"): "DLR.U",
        ("HORZNHIGINT", "CAD"): "CASH",
        ("ISHARESIBOXX", "USD"): "LQD",
        ("NURIENLTD", "CAD"): "NTR",
    }

    actual = {
        key: listing_for_text(key[0], key[1], institution_code="HSBC_IDI").symbol
        for key in expected
    }

    assert actual == expected


def test_unknown_name_is_queued_and_not_promoted_to_ticker(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    transaction = _transaction("MYSTERYLONGFUNDNAMEETF", "CAD")
    result = ParseResult(
        "hsbc",
        "fixture",
        statements=[ParsedStatement(
            account=ParsedAccount("HSBC-1", "Margin"),
            period_start="2024-01-01",
            period_end="2024-01-31",
            transactions=[transaction],
        )],
    )

    with sqlite_db.session(db_path) as conn:
        resolve_parse_result(conn, institution_code="HSBC_IDI", result=result)
        candidate = conn.execute(
            """
            SELECT normalized_text, status FROM instrument_resolution_candidates
            """
        ).fetchone()

    assert transaction.instrument is None
    assert transaction.resolution_method == "unresolved_printed_identity"
    assert tuple(candidate) == ("MYSTERYLONGFUNDNAMEETF", "pending")


def _persist_listing(conn, symbol: str, currency: str) -> int:
    listing = listing_for_symbol(symbol, currency)
    assert listing is not None
    return sqlite_db.upsert_instrument(
        conn,
        asset_type=listing.asset_type,
        symbol=listing.symbol,
        currency=listing.currency,
        exchange=listing.exchange,
        name=listing.security_name,
        issuer_key=listing.issuer_key,
        issuer_name=listing.issuer_name,
        security_key=listing.security_key,
        security_name=listing.security_name,
        journalable=listing.journalable,
        market_symbol=listing.yahoo_symbol,
    )


def test_currency_lines_share_security_but_keep_distinct_market_symbols(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        cad_id = _persist_listing(conn, "DLR", "CAD")
        usd_id = _persist_listing(conn, "DLR.U", "USD")
        _persist_listing(conn, "DLR", "CAD")
        _persist_listing(conn, "DLR.U", "USD")
        rows = conn.execute(
            """
            SELECT i.instrument_id, i.security_id, market.provider_symbol
              FROM instruments i
              JOIN instrument_market_symbols market
                ON market.instrument_id = i.instrument_id
             WHERE i.instrument_id IN (?, ?) ORDER BY i.instrument_id
            """,
            (cad_id, usd_id),
        ).fetchall()
        pair = conn.execute(
            """
            SELECT from_instrument_id, to_instrument_id, conversion_ratio
              FROM instrument_journal_pairs
            """
        ).fetchall()

    assert len({row["security_id"] for row in rows}) == 1
    assert {row["provider_symbol"] for row in rows} == {"DLR.TO", "DLR-U.TO"}
    assert len(pair) == 1
    assert {pair[0]["from_instrument_id"], pair[0]["to_instrument_id"]} == {cad_id, usd_id}
    assert pair[0]["conversion_ratio"] == 1.0


def test_cross_currency_journal_pair_matches_without_merging_listings(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
        cad_account = sqlite_db.upsert_account(
            conn, institution_id=institution_id, account_number="CAD", base_currency="CAD"
        )
        usd_account = sqlite_db.upsert_account(
            conn, institution_id=institution_id, account_number="USD", base_currency="USD"
        )
        cad_id = _persist_listing(conn, "DLR", "CAD")
        usd_id = _persist_listing(conn, "DLR.U", "USD")
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, trade_date, txn_type, instrument_id, quantity,
                position_delta, currency
            ) VALUES (?, '2024-01-10', 'journal', ?, -100, -100, 'CAD')
            """,
            (cad_account, cad_id),
        )
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, trade_date, txn_type, instrument_id, quantity,
                position_delta, currency
            ) VALUES (?, '2024-01-10', 'journal', ?, 100, 100, 'USD')
            """,
            (usd_account, usd_id),
        )

    assert link_transfers(db_path)["matched"] == 1


def test_market_refresh_targets_only_explicit_provider_symbols(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
        account_id = sqlite_db.upsert_account(
            conn, institution_id=institution_id, account_number="A-1"
        )
        instrument_id = _persist_listing(conn, "BCE", "CAD")
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, trade_date, txn_type, instrument_id, quantity,
                position_delta, currency
            ) VALUES (?, '2024-01-10', 'buy', ?, 1, 1, 'CAD')
            """,
            (account_id, instrument_id),
        )

    targets = _held_symbols(db_path)
    assert [(target.ledger_symbol, target.provider_symbol) for target in targets] == [
        ("BCE", "BCE.TO")
    ]


def test_yahoo_candidate_requires_unique_name_and_nonempty_history(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        sqlite_db.upsert_institution(conn, "HSBC_IDI", "HSBC")
        sqlite_db.queue_instrument_resolution_candidate(
            conn,
            institution_code="HSBC_IDI",
            normalized_text="EXAMPLECANADIANETF",
            display_text="Example Canadian ETF",
            asset_type="etf",
            currency="CAD",
        )

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: [{
            "symbol": "EXMP.TO",
            "shortname": "Example Canadian ETF",
            "quoteType": "ETF",
        }],
        history=lambda symbol: symbol == "EXMP.TO",
    )
    with sqlite_db.session(db_path) as conn:
        resolved = conn.execute(
            """
            SELECT candidate.status, instrument.symbol, market.provider_symbol,
                   market.status AS market_status
              FROM instrument_resolution_candidates candidate
              JOIN instruments instrument
                ON instrument.instrument_id = candidate.resolved_instrument_id
              JOIN instrument_market_symbols market
                ON market.instrument_id = instrument.instrument_id
            """
        ).fetchone()

    assert result == {"candidates_resolved": 1}
    assert tuple(resolved) == ("resolved", "EXMP", "EXMP.TO", "verified")


def test_yahoo_candidate_rejects_ambiguous_or_wrong_currency_results(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        sqlite_db.upsert_institution(conn, "HSBC_IDI", "HSBC")
        sqlite_db.queue_instrument_resolution_candidate(
            conn,
            institution_code="HSBC_IDI",
            normalized_text="EXAMPLEETF",
            display_text="Example ETF",
            asset_type="etf",
            currency="CAD",
        )

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: [
            {"symbol": "ONE", "shortname": "Example ETF", "quoteType": "ETF"},
            {"symbol": "TWO.TO", "shortname": "Example ETF", "quoteType": "ETF"},
            {"symbol": "THREE.TO", "shortname": "Example ETF", "quoteType": "ETF"},
        ],
        history=lambda _symbol: True,
    )
    with sqlite_db.session(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM instrument_resolution_candidates"
        ).fetchone()[0]

    assert result == {"candidate_ambiguous": 1}
    assert status == "ambiguous"

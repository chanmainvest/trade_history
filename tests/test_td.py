"""Self-contained tests for the TD parser."""
from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
from ledger.parsers.td import TDParser, _valid_td_option_token
from ledger.parsers.validation import validate_parse_result

from .fixture_loader import load_fixture


def test_td_modern_dual_account_holdings_activity_and_cash():
    result = TDParser().parse(load_fixture("td/modern_monthly.txt"))
    assert result.errors == []
    assert sorted(statement.account.account_number for statement in result.statements) == [
        "AB12CD-CAD",
        "AB12CD-USD",
    ]
    for statement in result.statements:
        assert statement.period_start == "2025-10-01"
        assert statement.period_end == "2025-10-31"
        assert statement.positions
        assert statement.transactions
        assert statement.cash_balances

    cad = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "CAD"
    )
    assert next(row for row in cad.transactions if row.txn_type == "buy").net_amount == -200.0

    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    option_positions = [
        position
        for position in usd.positions
        if position.instrument.asset_type == "option"
    ]
    assert option_positions
    short_position = next(
        position for position in usd.positions if position.instrument.symbol == "SHRT"
    )
    assert short_position.quantity == -2000.0
    assert short_position.market_price == 30.48
    assert short_position.market_value == -60960.0
    assert option_positions[0].instrument.option_expiry == "2026-02-20"
    assert any(
        transaction.instrument
        and transaction.instrument.asset_type == "option"
        for transaction in usd.transactions
    )
    adjusted_expiry = next(
        transaction
        for transaction in usd.transactions
        if transaction.txn_type == "option_expiration"
    )
    assert adjusted_expiry.instrument is not None
    # The printed OCC adjustment marker stays part of the root, matching the
    # holdings-table instrument for the same contract.
    assert adjusted_expiry.instrument.symbol == "BABA+$"
    assert adjusted_expiry.instrument.option_expiry == "2025-01-17"
    assert adjusted_expiry.quantity == -10
    assert validate_parse_result(result).is_valid


def test_td_name_only_buy_resolves_to_exact_same_statement_holding(tmp_path):
    result = TDParser().parse(load_fixture("td/modern_monthly.txt"))
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    velo_buy = next(
        transaction
        for transaction in usd.transactions
        if "VELO3D" in (transaction.description or "")
    )
    assert velo_buy.quantity == 2_000
    assert velo_buy.price == 25
    assert velo_buy.net_amount == -50_009.99
    assert velo_buy.instrument is not None
    assert velo_buy.instrument.name == "VELO3D INC-NEW"
    assert velo_buy.instrument.resolution_method == "unresolved_printed_identity"

    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        counts = resolve_parse_result(conn, institution_code="TD_WB", result=result)

    assert counts["same_statement_holding"] == 1
    assert velo_buy.instrument is not None
    assert velo_buy.instrument.symbol == "VELO"
    assert velo_buy.resolution_method == "same_statement_holding"


def test_td_legacy_bundle_splits_every_month_and_currency():
    result = TDParser().parse(load_fixture("td/legacy_bundle.txt"))
    assert result.errors == []
    assert len(result.statements) == 4
    assert {
        (statement.period_start, statement.period_end)
        for statement in result.statements
    } == {
        ("2016-01-01", "2016-01-31"),
        ("2016-02-01", "2016-02-29"),
    }
    assert {statement.account.account_number for statement in result.statements} == {
        "ZX90YU-CAD",
        "ZX90YU-USD",
    }
    assert all(statement.positions for statement in result.statements)
    assert all(statement.cash_balances for statement in result.statements)
    assert validate_parse_result(result).is_valid


def test_td_full_header_bundle_splits_every_month_with_complete_scopes():
    result = TDParser().parse(
        load_fixture("td/full_header_bundle_known_broken.txt")
    )
    assert result.errors == []
    assert {
        (statement.period_start, statement.period_end)
        for statement in result.statements
    } == {
        ("2020-01-01", "2020-01-31"),
        ("2020-02-01", "2020-02-29"),
    }
    assert all(
        {
            (scope.currency, scope.section_type, scope.completeness)
            for scope in statement.snapshot_sets
        } == {
            ("CAD", "cash", "complete"),
            ("CAD", "positions", "complete"),
        }
        for statement in result.statements
    )
    assert all(
        transaction.source_span and transaction.source_span.page_number == 1
        for statement in result.statements
        for transaction in statement.transactions
    )
    assert validate_parse_result(result).is_valid


def test_td_repeated_account_fragments_merge_into_one_scope_per_currency():
    result = TDParser().parse(load_fixture("td/repeated_account_fragment.txt"))
    assert result.errors == []
    assert len(result.statements) == 2
    cad = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "CAD"
    )
    assert len(cad.positions) == 1
    assert len(cad.transactions) == 2
    assert len(cad.cash_balances) == 1
    assert cad.page_numbers == (1, 3)
    assert cad.cash_balances[0].opening_balance == 100.0
    assert cad.cash_balances[0].closing_balance == 115.0
    assert cad.transactions[-1].source_span and cad.transactions[-1].source_span.page_number == 3
    assert {
        (scope.currency, scope.section_type, scope.completeness)
        for scope in cad.snapshot_sets
    } == {
        ("CAD", "cash", "complete"),
        ("CAD", "positions", "complete"),
    }
    assert all(scope.issues == [] for scope in cad.snapshot_sets)
    assert validate_parse_result(result).is_valid


def test_td_reported_balances_and_multiline_holdings_preserve_source_rows():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages[0] = pdf.pages[0].replace(
        "Account type: Direct Trading - CDN",
        """Account type: Direct Trading - CDN
Beginning balance $1,000.00
Change in your account $10.00
Ending balance $1,010.00
Cash 800.00 800.00 79.21%
Total equities $200.00 $210.00 20.79%""",
    )

    result = TDParser().parse(pdf)
    cad = next(row for row in result.statements if row.account.base_currency == "CAD")
    totals = {scope.section_type: scope for scope in cad.snapshot_sets}

    assert totals["positions"].reported_total == 210.0
    assert totals["cash"].reported_total == 800.0
    assert totals["summary"].opening_total == 1_000.0
    assert totals["summary"].reported_change == 10.0
    assert totals["summary"].reported_total == 1_010.0
    usd = next(row for row in result.statements if row.account.base_currency == "USD")
    velo = next(row for row in usd.positions if row.instrument.symbol == "VELO")
    assert velo.raw_line.splitlines() == [
        "VELO3D INC-NEW 2,000 SEG 25.000 50,000.00 50,000.00 0.00 10.00%",
        "(VELO)",
    ]


def test_td_disclosure_pages_are_not_owned_by_financial_statements():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages.append("Account number: AB12CD\nAccount type: Direct Trading - US\nDisclosures")
    pdf.page_count += 1

    result = TDParser().parse(pdf)

    assert all(pdf.page_count not in statement.page_numbers for statement in result.statements)


def test_td_option_holding_skips_harmless_intervening_header_lines():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "\n20FE@35",
            "\nPage 1 of 2\nDescription Quantity\n20FE@35",
        )
        for page in pdf.pages
    ]
    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    option = next(
        position.instrument
        for position in usd.positions
        if position.instrument.asset_type == "option"
    )
    assert option.option_expiry == "2026-02-20"
    assert validate_parse_result(result).is_valid


def test_td_signed_close_month_code_and_pending_activity_boundary():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "Ending cash balance $360.00",
            """Oct 22 Withholding tax BETA CORP 5 -10.00 350.00
Oct 23 Assign PUT -100 BBB'25 24FB@30 1 0.00 350.00
Ending cash balance -$350.00
Pending activity in your account this period
Nov 1 Buy BETA CORP (BBB) 1 100.00 -100.00 250.00
Ending cash balance $250.00""",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    tax = next(row for row in usd.transactions if row.txn_type == "tax_withholding")
    assignment = next(
        row for row in usd.transactions if row.txn_type == "option_assignment"
    )

    assert tax.net_amount == -10.0
    assert assignment.instrument is not None
    assert assignment.instrument.option_expiry == "2025-02-24"
    assert usd.cash_balances[0].closing_balance == -350.0
    assert not any(row.trade_date == "2025-11-01" for row in usd.transactions)
    assert validate_parse_result(result).is_valid


def test_td_disposition_cash_events_and_in_kind_transfer():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "Ending cash balance $360.00",
            """Oct 22 Disposition BETA CORP (BBB) -5 150.00 510.00
Oct 23 Web Banking AB123 TSF FR 9999999 1,000.00 1,510.00
Oct 24 Admin FeeCharged PAPER STATEMENT FEE 2.00 1,508.00
Oct 25 CIL BETA CORP 5.00 1,513.00
Oct 26 Transfer In BETA CORP (BBB) 10 0.00 1,513.00
Ending cash balance $1,513.00""",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    disposition = next(
        row for row in usd.transactions if row.description.startswith("BETA CORP")
        and row.txn_type == "sell"
        and row.trade_date == "2025-10-22"
    )
    transfer = next(
        row for row in usd.transactions if row.trade_date == "2025-10-26"
    )

    assert (disposition.quantity, disposition.price, disposition.net_amount) == (
        -5.0,
        None,
        150.0,
    )
    assert next(row for row in usd.transactions if row.trade_date == "2025-10-23").net_amount == 1000.0
    assert next(row for row in usd.transactions if row.trade_date == "2025-10-24").net_amount == -2.0
    assert next(row for row in usd.transactions if row.trade_date == "2025-10-25").net_amount == 5.0
    assert transfer.txn_type == "transfer_in"
    assert transfer.quantity == 10.0
    assert transfer.net_amount == 0.0
    assert transfer.instrument is not None
    assert transfer.instrument.symbol == "BBB"
    assert validate_parse_result(result).is_valid


def test_td_income_preserves_printed_sign_and_canonicalizes_unsigned():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "Ending cash balance $360.00",
            """Oct 20 Interest INTEREST TO OCT 20 -21.08 338.92
Oct 21 Dividend BETA CORP (BBB) 10.00 348.92
Ending cash balance $348.92""",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    interest = next(
        row for row in usd.transactions if row.txn_type == "interest_income"
    )
    dividend = next(row for row in usd.transactions if row.txn_type == "dividend")

    # The printed -21.08 is source evidence; the unsigned dividend keeps the
    # canonical inflow direction.
    assert interest.net_amount == -21.08
    assert dividend.net_amount == 10.0
    assert validate_parse_result(result).is_valid


def test_td_drip_echo_recorded_as_reinvestment_on_printing_statement():
    pdf = load_fixture("td/modern_monthly.txt")
    # TD prints each month-end DRIP reinvestment once, on the next month's
    # statement, dated the prior pay date with $0.00 cash. It is the only
    # appearance of that row, so it is recorded here rather than quarantined.
    pdf.pages = [
        page.replace(
            "Beginning cash balance $500.00\nOct 6 Sell",
            """Beginning cash balance $500.00
Sep 30 Dividend TD DIV INCM-D /NL'FRAC 2.526 0.00 500.00
Reinvestment Plan VALUE = 47.86
Oct 6 Sell""",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    echo = next(
        row for row in usd.transactions if row.txn_type == "reinvest_dividend"
    )
    assert echo.trade_date == "2025-09-30"
    assert echo.quantity == 2.526
    assert echo.net_amount == 0.0
    assert echo.instrument is not None
    assert echo.instrument.asset_type == "mutual_fund"
    assert "Reinvestment Plan VALUE = 47.86" in (echo.description or "")
    assert all("TD DIV INCM" not in (row.raw_line or "") for row in usd.quarantine)
    assert validate_parse_result(result).is_valid


def test_td_cash_dividend_echo_still_quarantines_out_of_period():
    pdf = load_fixture("td/modern_monthly.txt")
    # A cash-carrying echo repeats a dividend the prior statement already
    # recorded, so it stays in quarantine as a duplicate.
    pdf.pages = [
        page.replace(
            "Ending cash balance $360.00",
            """Sep 29 Dividends TORONTO DOMINION BANK 100 40.00 400.00
Ending cash balance $400.00""",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    assert not any(
        row.txn_type == "dividend" and row.raw_line.startswith("Sep 29")
        for row in usd.transactions
    )
    quarantined = next(
        row for row in usd.quarantine if "TORONTO DOMINION" in (row.raw_line or "")
    )
    assert "outside the statement period" in quarantined.reason
    assert validate_parse_result(result).is_valid


def test_td_unknown_numeric_activity_marks_cash_scope_incomplete():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "Ending cash balance $360.00",
            "Oct 22 Mystery Event 10.00 370.00\nEnding cash balance $370.00",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )

    assert any("unknown verb" in row.reason for row in usd.quarantine)
    assert next(
        scope for scope in usd.snapshot_sets if scope.section_type == "cash"
    ).completeness == "unknown"
    cash_scope = next(
        scope for scope in usd.snapshot_sets if scope.section_type == "cash"
    )
    assert cash_scope.issues
    assert cash_scope.issues[0].blocks_completeness


def test_td_unrecognized_numeric_holding_marks_position_scope_incomplete():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "(SHRT)\nOptions",
            "(SHRT)\n...2..0..0....CORRUPTED...ROW...5..0..0...\nOptions",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )

    assert any("unrecognized holding row" in row.reason for row in usd.quarantine)
    assert next(
        scope for scope in usd.snapshot_sets if scope.section_type == "positions"
    ).completeness == "unknown"


def test_td_legacy_trade_reads_leading_quantity_before_name():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "Ending cash balance $360.00",
            """Oct 22 Sell -132.957 TD JPN INDEX-I SER/NL'FRAC 9.660 1,284.36 1,644.36
Ending cash balance $1,644.36""",
        )
        for page in pdf.pages
    ]

    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    row = next(transaction for transaction in usd.transactions if transaction.trade_date == "2025-10-22")

    assert row.txn_type == "sell"
    assert row.quantity == -132.957
    assert row.price == 9.66
    assert row.net_amount == 1284.36
    assert row.instrument is not None
    assert row.instrument.name == "TD JPN INDEX-I SER/NL'FRAC"


def test_td_summary_filename_emits_annual_statement():
    result = TDParser().parse(
        load_fixture("td/Statement_AB12CD_2023_summary.txt")
    )
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.statement_type == "annual"
    assert statement.period_start == "2023-01-01"
    assert statement.period_end == "2023-12-31"


def test_td_option_token_rejects_invalid_month_codes():
    assert _valid_td_option_token("26", "13", "FB") is True
    assert _valid_td_option_token("26", "", "FB") is True
    assert _valid_td_option_token("26", "13", "GD") is False
    assert _valid_td_option_token("26", "32", "FB") is False


def test_td_summary_preserves_minus_before_dollar_sign():
    # TD prints negative account changes as "-$24,175.14"; the summary
    # capture must keep the printed sign (July 2026 58MRB0-USD).
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages[0] = pdf.pages[0].replace(
        "Account type: Direct Trading - CDN",
        """Account type: Direct Trading - CDN
Beginning balance $2,505,026.94
Change in your account -$24,175.14
Ending balance $2,480,851.80""",
    )

    result = TDParser().parse(pdf)
    cad = next(row for row in result.statements if row.account.base_currency == "CAD")
    summary = next(
        scope for scope in cad.snapshot_sets if scope.section_type == "summary"
    )

    assert summary.opening_total == 2_505_026.94
    assert summary.reported_change == -24_175.14
    assert summary.reported_total == 2_480_851.80


def test_td_split_swap_legs_furniture_and_adjusted_option_activity():
    # May 2026 58MRB0-CAD: a reverse split prints two book-swap legs with
    # the signed share counts inside the security name; a fractional-
    # residue journal prints an internal vehicle name and 0.00 cash; an
    # adjusted option root ("98TRI+$") appears in activity rows.
    result = TDParser().parse(load_fixture("td/split_and_furniture.txt"))
    by_period = {statement.period_start: statement for statement in result.statements}
    assert not any(statement.quarantine for statement in result.statements)

    march = by_period["2026-03-01"]
    # RBF-prefixed broker fund codes on holdings wraps carry printed
    # fund-code identity, exactly like the TDB codes.
    for position in march.positions:
        assert position.instrument.resolution_method == "printed_fund_code"
        assert position.instrument.symbol in {"RBF607C", "RBF610C"}
    exchange_in, exchange_out = march.transactions
    assert exchange_in.txn_type == "journal"
    assert exchange_in.quantity == 2_660.453
    assert exchange_in.net_amount == 0.0
    assert exchange_in.instrument.symbol == "RBF678C"
    assert exchange_out.txn_type == "journal"
    assert exchange_out.quantity == -967.364
    assert exchange_out.instrument.symbol == "RBF610C"

    april = by_period["2026-04-01"]
    position_note = april.transactions[0]
    assert position_note.txn_type == "adjustment"
    assert position_note.quantity is None
    assert position_note.net_amount == 0.0
    assert position_note.instrument is None

    may = by_period["2026-05-01"]
    out_leg, in_leg, split_note = may.transactions
    assert out_leg.txn_type == "journal"
    assert out_leg.quantity == -100.0
    assert out_leg.net_amount == 12_196.99
    assert out_leg.instrument.symbol == "TRI"
    assert in_leg.txn_type == "journal"
    assert in_leg.quantity == 98.0
    assert in_leg.net_amount == -12_196.99
    assert in_leg.instrument.symbol == "TRI"
    # A 0.00 split note prints the resulting total, not a delta: the
    # quantity stays uncaptured and the row keeps the stock_split type.
    assert split_note.txn_type == "stock_split"
    assert split_note.quantity is None
    assert split_note.net_amount == 0.0

    june = by_period["2026-06-01"]
    transfer = next(
        transaction
        for transaction in june.transactions
        if transaction.txn_type == "transfer_out"
    )
    expiration = next(
        transaction
        for transaction in june.transactions
        if transaction.txn_type == "option_expiration"
    )
    fund_sell = next(
        transaction
        for transaction in june.transactions
        if transaction.txn_type == "sell"
        and transaction.raw_line.startswith("Jun 19 Sell TD CDN EQ-D")
    )
    # A TD fund name printed without its code resolves through the reviewed
    # name catalog (holdings pair name and TDB#### code on the same row).
    assert fund_sell.instrument.symbol == "TDB3089C"
    assert fund_sell.quantity == -2_792.832
    assert transfer.txn_type == "transfer_out"
    assert transfer.quantity == -10_000.0
    assert transfer.net_amount == 0.0
    assert transfer.instrument.symbol == "DLR"
    assert expiration.txn_type == "option_expiration"
    assert expiration.quantity == -1.0
    assert expiration.instrument.symbol == "98TRI+$"
    assert expiration.instrument.option_strike == 120.0
    assert expiration.instrument.option_expiry == "2026-06-19"
    assert expiration.instrument.option_type == "CALL"


def test_td_interest_memo_row_never_claims_an_instrument(tmp_path):
    # "Interest INTEREST TO JUL 16 -87.81" is a cash memo: deliberately
    # stored without an instrument and without the unresolved-identity
    # marker (July 2026 58MRB0 July statements).
    result = TDParser().parse(load_fixture("td/split_and_furniture.txt"))
    july = next(
        statement
        for statement in result.statements
        if statement.period_start == "2026-07-01"
    )
    assert not july.quarantine
    memo = july.transactions[0]
    assert memo.txn_type == "interest_income"
    assert memo.instrument is None
    assert memo.net_amount == -87.81
    # Abbreviated equity names on dividend rows resolve through the reviewed
    # name catalog (holdings wrap lines print the symbols on the same page).
    pac_div = next(
        transaction
        for transaction in july.transactions
        if transaction.txn_type == "dividend"
        and "CANADIAN PAC" in (transaction.raw_line or "")
    )
    cm_div = next(
        transaction
        for transaction in july.transactions
        if transaction.txn_type == "dividend"
        and "IMPERIAL" in (transaction.raw_line or "")
    )
    assert pac_div.instrument.symbol == "CP"
    assert cm_div.instrument.symbol == "CM"
    su_div = next(
        transaction
        for transaction in july.transactions
        if "SUNCOR" in (transaction.raw_line or "")
    )
    cnr_div = next(
        transaction
        for transaction in july.transactions
        if "CANADIAN NATIONAL" in (transaction.raw_line or "")
    )
    assert su_div.instrument.symbol == "SU"
    assert cnr_div.instrument.symbol == "CNR"

    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        resolve_parse_result(conn, institution_code="TD_WB", result=result)
    assert memo.resolution_method is None


def test_td_legacy_seg_holdings_and_exchange_verbs():
    result = TDParser().parse(load_fixture("td/legacy_seg_holdings.txt"))
    assert result.errors == []
    jan, feb, mar = (
        result.statements[0], result.statements[1], result.statements[2],
    )

    # January: 2016-era "qty Seg NAME SYM tail" rows parse; the printed
    # FundServ code types the row as a mutual fund even though no section
    # header matched, and footnote/total/header furniture is skipped.
    jan_syms = {p.instrument.symbol: p for p in jan.positions}
    assert set(jan_syms) == {"BMO", "TDB628C"}
    tdb = jan_syms["TDB628C"]
    assert tdb.instrument.asset_type == "mutual_fund"
    assert tdb.instrument.resolution_method == "printed_fund_code"
    assert tdb.quantity == 194.256
    assert all(
        "4U=" not in (q.raw_line or "")
        and "Totalportfolio" not in (q.raw_line or "")
        and "Direct Trading" not in (q.raw_line or "")
        for q in jan.quarantine
    )
    # The symbol-less N/D row has no printed identity to persist.
    assert any("BATTERYTECHNOLOGIESINC" in q.raw_line for q in jan.quarantine)

    # February: N/D name-first rows parse with their wrapped names; cells
    # the statement prints as N/D stay uncaptured, but the position and its
    # book value persist. The ticker wrap supplies the symbol.
    feb_syms = {p.instrument.symbol: p for p in feb.positions}
    assert "CM" in feb_syms
    cm = feb_syms["CM"]
    assert cm.quantity == 1200.0
    assert cm.book_value == 61469.99
    assert cm.market_value == 126468.00
    assert "CDN IMPERIAL BK" in (cm.instrument.name or "")

    # Exchange legs record both printed facts: the signed unit movement and
    # the transferred value (the trailing number is a running balance).
    exchanges = [
        t for t in feb.transactions
        if t.txn_type == "journal" and "Exchange" in (t.raw_line or "")
    ]
    assert [(t.quantity, t.net_amount) for t in exchanges] == [
        (2379.892, -22516.16),
        (-769.017, 22516.16),
    ]
    # The defunct removal is an in-kind journal at the printed 0.00 price.
    defunct = next(
        t for t in feb.transactions
        if "Defunct Security" in (t.raw_line or "")
    )
    assert defunct.quantity == -1000.0
    assert defunct.net_amount == 0.0
    # The exchange pair nets to zero, so the cash chain still closes.
    assert feb.cash_balances[0].opening_balance == 5000.0
    assert feb.cash_balances[0].closing_balance == 5000.0
    assert validate_parse_result(result).is_valid

    # March: the 2016-era two-line Web Banking rows promote the cash amount
    # printed on their continuation line, with the continuation's verb
    # fixing the direction; the split DRIP row reinvests the printed units.
    mar_txns = mar.transactions
    deposit = next(t for t in mar_txns if t.txn_type == "transfer_in")
    assert deposit.net_amount == 10000.0
    assert "RX534TSFFR3301767" in (deposit.description or "")
    withdrawal = next(t for t in mar_txns if t.txn_type == "transfer_out")
    assert withdrawal.net_amount == -10.0
    drip = next(t for t in mar_txns if t.txn_type == "reinvest_dividend")
    assert drip.quantity == 0.553
    assert drip.net_amount is None
    assert drip.instrument is not None
    assert "TDCDNMNY" in (drip.instrument.name or "")
    assert "VALUE= 5.53" in (drip.description or "")
    assert mar.cash_balances[0].opening_balance == 5000.0
    assert mar.cash_balances[0].closing_balance == 14988.0


def test_td_december_annual_performance_report_is_its_own_statement():
    result = TDParser().parse(load_fixture("td/annual_performance_page.txt"))
    assert result.errors == []
    monthly = [s for s in result.statements if s.statement_type == "monthly"]
    annual = [s for s in result.statements if s.statement_type == "annual"]
    assert len(monthly) == 1 and len(annual) == 1
    # The performance page is excluded from the monthly statement: the
    # year-long "January 1 to December 31" header on that page must not
    # create a second monthly statement, and the annual record owns the page.
    assert monthly[0].page_numbers == (1,)
    assert monthly[0].period_start == "2026-12-01"
    assert annual[0].period_start == "2026-01-01"
    assert annual[0].period_end == "2026-12-31"
    assert annual[0].page_numbers == (2,)
    perf = annual[0].annual_performance
    assert len(perf) == 1
    row = perf[0]
    assert row.currency == "CAD"
    assert row.since_date == "2023-01-30"
    assert row.beginning_market_value == 1000.0
    assert row.deposits_transfers_in == 5.0
    assert row.withdrawals_transfers_out == 0.0
    assert row.net_investment_return == 10.0
    assert row.ending_market_value == 1015.0
    assert row.money_weighted_1y == 3.50
    assert row.money_weighted_since == 1.20
    assert validate_parse_result(result).is_valid

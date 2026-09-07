"""Self-contained tests for the RBC parser."""
from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
from ledger.ingest.pipeline import _record_source_file, _write_statement
from ledger.parsers.rbc import RBCParser
from ledger.parsers.validation import validate_parse_result
from ledger.pdf_text import PdfLine, PdfWord

from .fixture_loader import load_fixture


def test_rbc_dual_currency_blocks_form_one_statement_with_complete_scopes():
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.account.account_number == "111-22222-3-4"
    assert statement.account.base_currency == "CAD"
    assert statement.period_start == "2026-01-01"
    assert statement.period_end == "2026-01-30"
    assert {
        (scope.currency, scope.section_type, scope.completeness)
        for scope in statement.snapshot_sets
    } == {
        ("CAD", "cash", "complete"),
        ("CAD", "positions", "complete"),
        ("USD", "cash", "complete"),
        ("USD", "positions", "complete"),
    }
    assert validate_parse_result(result).is_valid

    # A legacy DRIP reinvestment prints as its own dated row with the unit
    # count AND the cash debit it moved: it records as a journal leg with
    # both facts, not a quantity-less adjustment.
    drip = next(
        row for row in statement.transactions
        if "DIVREIN" in (row.description or "")
    )
    assert drip.txn_type == "journal"
    assert drip.quantity == 1.0
    assert drip.net_amount == -12.00
    assert drip.instrument is not None
    assert drip.instrument.name == "ALPHACORP"
    assert "REINV@U$12.0000" in drip.description


def test_rbc_compact_month_day_activity_is_not_dropped():
    result = RBCParser().parse(load_fixture("rbc/compact_month_day_activity.txt"))

    assert result.errors == []
    statement = result.statements[0]
    buys = [row for row in statement.transactions if row.txn_type == "buy"]
    assert [(row.trade_date, row.quantity, row.price) for row in buys] == [
        ("2021-08-10", 14000.0, 14.31),
        ("2021-08-20", 3000.0, 15.36),
        ("2021-08-20", 300.0, 31.463),
        ("2021-08-20", 700.0, 31.45),
        ("2021-08-23", 5000.0, 20.0),
        ("2021-08-25", 1000.0, 48.8),
        ("2021-08-25", 600.0, 76.84),
    ]
    deposit = next(row for row in statement.transactions if row.txn_type == "deposit")
    assert deposit.net_amount == 49000.0
    assert len(statement.transactions) == 8
    assert all(row.txn_type != "adjustment" for row in statement.transactions)
    assert statement.cash_balances[0].opening_balance == 425490.05
    assert statement.cash_balances[0].closing_balance == 1642.4


def test_rbc_full_month_name_activity_is_not_dropped():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    # Some statement months print the full month name in activity dates
    # ("JULY 31 DIVIDEND ..."). Those rows must parse, not silently vanish.
    pdf.pages = [
        page.replace(
            "JAN. 05 DIVIDEND ALPHA CORP 50.00",
            "JANUARY 05 DIVIDEND ALPHA CORP 50.00",
        )
        for page in pdf.pages
    ]
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    dividends = [row for row in statement.transactions if row.txn_type == "dividend"]
    assert [(row.trade_date, row.net_amount) for row in dividends] == [
        ("2026-01-05", 50.0),
    ]


def test_rbc_holdings_dividend_option_and_cash():
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    statement = result.statements[0]
    cad_positions = [row for row in statement.positions if row.currency == "CAD"]
    assert {row.instrument.asset_type for row in cad_positions} == {
        "equity",
        "mutual_fund",
    }
    dividend = next(
        row
        for row in statement.transactions
        if row.txn_type == "dividend" and row.currency == "CAD"
    )
    assert dividend.net_amount == 50.0
    assert next(cash for cash in statement.cash_balances if cash.currency == "CAD").closing_balance == 1043.0

    option_transactions = [
        row
        for row in statement.transactions
        if row.instrument and row.instrument.asset_type == "option"
    ]
    assert option_transactions
    option = option_transactions[0].instrument
    assert option.option_expiry == "2026-02-20"
    assert option.option_strike == 35.0
    assert option.option_type == "CALL"
    exercise = next(
        row for row in option_transactions if row.txn_type == "option_exercise"
    )
    assert exercise.instrument is not None
    assert exercise.instrument.symbol == "TRP"
    assert exercise.quantity == -20
    assert all(row.source_span for row in statement.transactions)


def test_rbc_reinvested_fund_dividend_is_units_not_cash(tmp_path):
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    pdf.pages[0] = pdf.pages[0].replace(
        "SYNTHETIC FUND SYNF 100 10.000 1,000.00 $1,000.00",
        "SYNTHETIC FUND RBF123 100 10.000 1,000.00 $1,000.00",
    ).replace(
        "JAN. 05 DIVIDEND ALPHA CORP 50.00",
        """JAN. 05 DIVIDEND SYNTHETIC FUND 2.500
SR F (123)
REINVEST @ $10.0000""",
    )

    result = RBCParser().parse(pdf)
    row = next(
        transaction
        for transaction in result.statements[0].transactions
        if transaction.txn_type == "reinvest_dividend"
    )

    assert row.instrument is not None
    assert row.instrument.symbol == "RBF123"
    assert row.quantity == 2.5
    assert row.price == 10.0
    assert row.net_amount == 0.0
    assert row.cash_delta == 0.0
    assert len(row.raw_line.splitlines()) == 3

    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        resolve_parse_result(conn, institution_code="RBC_DI", result=result)
    assert row.instrument is not None
    assert row.instrument.symbol == "RBF123"
    assert row.resolution_method == "printed_fund_code"


def test_rbc_annual_performance_report():
    result = RBCParser().parse(load_fixture("rbc/2022_annual_report.txt"))
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.statement_type == "annual"
    assert statement.period_start == "2022-01-01"
    assert statement.period_end == "2022-12-31"
    rows = {row.currency: row for row in statement.annual_performance}
    assert rows["CAD"].ending_market_value == 103000.0
    assert rows["CAD"].money_weighted_1y == -2.0
    assert rows["USD"].since_date == "2022-03-28"
    assert rows["USD"].ending_market_value == 15900.0
    assert rows["USD"].money_weighted_since == -20.0


def test_rbc_layout_columns_control_cash_signs():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")

    def word(text: str, x0: float, x1: float) -> PdfWord:
        return PdfWord(text=text, x0=x0, top=10, x1=x1, bottom=20)

    header = PdfLine(
        page_number=1,
        line_number=1,
        text="DATE ACTIVITY DESCRIPTION QUANTITY RATE DEBIT CREDIT",
        words=(
            word("RATE", 390, 420),
            word("DEBIT", 470, 495),
            word("CREDIT", 540, 570),
        ),
    )
    interest = PdfLine(
        page_number=1,
        line_number=2,
        text="JAN. 06 INTEREST CASH 5.00",
        words=(
            word("JAN.", 35, 55),
            word("06", 60, 70),
            word("INTEREST", 75, 115),
            word("CASH", 130, 155),
            word("5.00", 475, 495),
        ),
    )
    pdf.page_lines = [[header, interest], []]

    result = RBCParser().parse(pdf)
    row = next(
        transaction
        for transaction in result.statements[0].transactions
        if transaction.description == "INTEREST CASH 5.00"
    )

    assert row.txn_type == "interest_expense"
    assert row.net_amount == -5.0


def test_rbc_layout_nets_withholding_debit_against_dividend_credit():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")

    def word(text: str, x0: float, x1: float) -> PdfWord:
        return PdfWord(text=text, x0=x0, top=10, x1=x1, bottom=20)

    line_text = "JAN. 06 DIVIDEND BCE INC 0.2944 176.64 1,177.60"
    pdf.page_lines = [[
        PdfLine(
            page_number=1,
            line_number=1,
            text="DATE ACTIVITY DESCRIPTION QUANTITY RATE DEBIT CREDIT",
            words=(
                word("RATE", 390, 420),
                word("DEBIT", 470, 495),
                word("CREDIT", 540, 570),
            ),
        ),
        PdfLine(
            page_number=1,
            line_number=2,
            text=line_text,
            words=(
                word("0.2944", 390, 425),
                word("176.64", 475, 510),
                word("1,177.60", 545, 590),
            ),
        ),
    ], []]
    pdf.pages = [
        page.replace("JAN. 06 INTEREST CASH 5.00", line_text)
        for page in pdf.pages
    ]

    result = RBCParser().parse(pdf)
    row = next(
        transaction
        for transaction in result.statements[0].transactions
        if transaction.description == "DIVIDEND BCE INC 0.2944 176.64 1,177.60"
    )

    assert row.net_amount == 1000.96


def test_rbc_layout_keeps_in_kind_transfer_out_of_cash():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")

    def word(text: str, x0: float, x1: float) -> PdfWord:
        return PdfWord(text=text, x0=x0, top=10, x1=x1, bottom=20)

    line_text = "JAN. 06 TRANSFER NUTRIEN LTD 3,000-"
    pdf.page_lines = [[
        PdfLine(
            page_number=1,
            line_number=1,
            text="DATE ACTIVITY DESCRIPTION QUANTITY RATE DEBIT CREDIT",
            words=(
                word("RATE", 390, 420),
                word("DEBIT", 470, 495),
                word("CREDIT", 540, 570),
            ),
        ),
        PdfLine(
            page_number=1,
            line_number=2,
            text=line_text,
            words=(word("3,000-", 330, 375),),
        ),
    ], []]
    pdf.pages = [
        page.replace("JAN. 06 INTEREST CASH 5.00", line_text)
        for page in pdf.pages
    ]

    result = RBCParser().parse(pdf)
    row = next(
        transaction
        for transaction in result.statements[0].transactions
        if transaction.description == "TRANSFER NUTRIEN LTD 3,000-"
    )

    assert row.txn_type == "transfer_out"
    assert row.quantity == -3000.0
    assert row.net_amount == 0.0
    assert row.cash_delta == 0.0
    assert row.instrument is not None
    assert row.instrument.symbol == "NTR"


def test_rbc_layout_parses_nominal_cost_buy_without_unit_price():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")

    def word(text: str, x0: float, x1: float) -> PdfWord:
        return PdfWord(text=text, x0=x0, top=10, x1=x1, bottom=20)

    line_text = "JAN. 06 BOUGHT SOUTH BOW CORP 400 0.01"
    pdf.page_lines = [[
        PdfLine(
            page_number=1,
            line_number=1,
            text="DATE ACTIVITY DESCRIPTION QUANTITY RATE DEBIT CREDIT",
            words=(
                word("RATE", 390, 420),
                word("DEBIT", 470, 495),
                word("CREDIT", 540, 570),
            ),
        ),
        PdfLine(
            page_number=1,
            line_number=2,
            text=line_text,
            words=(word("400", 330, 360), word("0.01", 475, 495)),
        ),
    ], []]
    pdf.pages = [
        page.replace("JAN. 06 INTEREST CASH 5.00", line_text)
        for page in pdf.pages
    ]

    result = RBCParser().parse(pdf)
    row = next(
        transaction
        for transaction in result.statements[0].transactions
        if transaction.description == "BOUGHT SOUTH BOW CORP 400 0.01"
    )

    assert row.txn_type == "buy"
    assert row.quantity == 400.0
    assert row.price is None
    assert row.net_amount == -0.01


def test_rbc_layout_parses_transfer_reference_and_unlabelled_cash():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")

    def word(text: str, x0: float, x1: float) -> PdfWord:
        return PdfWord(text=text, x0=x0, top=10, x1=x1, bottom=20)

    transfer_text = "JAN. 06 TRFIN146 ACCOUNT TRANSFER 50,000.00"
    adjustment_text = "JAN. 07 MACKENZIE US TIPS INDEX 0.205 1,189.99"
    pdf.page_lines = [[
        PdfLine(
            page_number=1,
            line_number=1,
            text="DATE ACTIVITY DESCRIPTION QUANTITY RATE DEBIT CREDIT",
            words=(
                word("RATE", 390, 420),
                word("DEBIT", 470, 495),
                word("CREDIT", 540, 570),
            ),
        ),
        PdfLine(
            page_number=1,
            line_number=2,
            text=transfer_text,
            words=(word("50,000.00", 540, 590),),
        ),
        PdfLine(
            page_number=1,
            line_number=3,
            text=adjustment_text,
            words=(word("0.205", 390, 425), word("1,189.99", 540, 590)),
        ),
    ], []]
    pdf.pages = [
        page.replace(
            "JAN. 06 INTEREST CASH 5.00",
            transfer_text + "\n" + adjustment_text,
        )
        for page in pdf.pages
    ]

    result = RBCParser().parse(pdf)
    transfer = next(
        row for row in result.statements[0].transactions
        if row.raw_line == transfer_text
    )
    adjustment = next(
        row for row in result.statements[0].transactions
        if row.raw_line == adjustment_text
    )

    assert transfer.txn_type == "transfer_in"
    assert transfer.net_amount == 50_000.0
    assert adjustment.txn_type == "adjustment"
    assert adjustment.net_amount == 1189.99


def test_rbc_asset_review_skips_page_furniture():
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    assert result.errors == []
    statement = result.statements[0]
    # Statement-year and account-number/page lines inside Asset Review are
    # page furniture: never quarantined, never row data.
    assert all(
        "Dollar Statement" not in row.raw_line
        and "Your Account Number" not in row.raw_line
        for row in statement.quarantine
    )
    # The holdings printed around the furniture still parse.
    assert {pos.instrument.symbol for pos in statement.positions} >= {
        "AAA", "SYNF", "BBB",
    }


def test_rbc_asset_review_ignores_order_execution_only_header():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    # On multi-page holdings the repeated "Order Execution Only <MMM. DD>"
    # page header lands inside Asset Review; its date makes it a numeric
    # candidate, but it is page furniture, never a holding row.
    pdf.pages = [
        page.replace(
            "Mutual Funds\n",
            "Order Execution Only APR. 30\nMutual Funds\n",
        )
        for page in pdf.pages
    ]
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    assert all(
        "Order Execution Only" not in row.raw_line
        for row in statement.quarantine
    )
    assert {pos.instrument.symbol for pos in statement.positions} >= {
        "AAA", "SYNF", "BBB",
    }


def test_rbc_holding_line_ignores_footnote_marker():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    # RBC prints footnote markers between the value columns (e.g. "#" marks a
    # book cost obtained from a non-RBC source); the row must still parse as
    # a holding and the printed line is preserved verbatim.
    pdf.pages = [
        page.replace(
            "ALPHA CORP AAA 10 20.000 200.00 $200.00",
            "ALPHA CORP AAA 10 20.000 200.00 # $200.00",
        )
        for page in pdf.pages
    ]
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    alpha = next(
        pos for pos in statement.positions if pos.instrument.symbol == "AAA"
    )
    assert (alpha.book_value, alpha.market_value) == (200.0, 200.0)
    assert "ALPHA CORP" not in " ".join(
        row.raw_line or "" for row in statement.quarantine
    )
    assert alpha.raw_line.endswith("# $200.00")


def test_rbc_other_section_holds_securities_beside_options():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    # RBC prints miscellaneous securities (e.g. BHP depositary shares) in
    # the standard holding-row shape under "Other", next to option
    # contracts; the section is not options-only.
    pdf.pages = [
        page.replace(
            "Other\nCALL .BBB",
            "Other\nBHP GROUP LIMITED BHP 5 30.000 150.00 $150.00\nCALL .BBB",
        )
        for page in pdf.pages
    ]
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    bhp = next(
        pos for pos in statement.positions
        if pos.instrument and pos.instrument.symbol == "BHP"
    )
    assert bhp.instrument.asset_type == "equity"
    assert (bhp.quantity, bhp.market_value) == (5.0, 150.0)
    # The option printed in the same section still parses as an option.
    call = next(
        pos for pos in statement.positions
        if pos.instrument and pos.instrument.asset_type == "option"
    )
    assert call.instrument.option_root == "BBB"
    assert all("BHP" not in (row.raw_line or "") for row in statement.quarantine)


def test_rbc_merger_and_exchange_legs_print_in_kind_quantities():
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    statement = result.statements[0]

    mergers = [row for row in statement.transactions if row.txn_type == "merger"]
    assert sorted(row.quantity for row in mergers) == [-1000.0, 1583.0]
    assert all(row.net_amount is None for row in mergers)
    assert all(row.cash_delta == 0.0 for row in mergers)
    # The printed verb is stripped from the security identity.
    assert all(
        not (row.instrument and row.instrument.name or "").startswith("MGR")
        for row in mergers
    )
    # The surrendered leg identifies its fund by the printed broker code.
    out_leg = next(row for row in mergers if row.quantity < 0)
    assert out_leg.instrument.symbol == "RBF123"
    assert out_leg.instrument.asset_type == "mutual_fund"
    assert out_leg.instrument.resolution_method == "printed_fund_code"

    exchanges = [
        row for row in statement.transactions
        if row.txn_type == "journal" and "DIVREIN" not in (row.description or "")
    ]
    assert len(exchanges) == 1
    # Text-only fixtures carry no debit/credit columns, so the in-kind journal
    # quantity arrives via layout effects in production; the verb itself and
    # the printed value still parse here through the amount fallback.
    assert exchanges[0].net_amount == -0.021


def test_rbc_asset_review_without_activity_still_owns_positions():
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    # A month with no CAD trades prints no Account Activity for the currency;
    # the Asset Review block must still declare the CAD positions scope.
    pdf.pages = [
        page.replace(
            "Account Activity\nOpening Balance (Dec. 31) $1,000.00\n"
            "JAN. 05 DIVIDEND ALPHA CORP 50.00",
            "",
        ).replace(
            "Account Activity\nJAN. 06 INTEREST CASH 5.00\n"
            "Closing Balance (Jan. 30) $1,055.00",
            "",
        )
        for page in pdf.pages
    ]
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    scopes = {
        (scope.currency, scope.section_type): scope.completeness
        for scope in statement.snapshot_sets
    }
    assert scopes[("CAD", "positions")] == "complete"
    assert {pos.instrument.symbol for pos in statement.positions} >= {"AAA", "SYNF"}


def _pages_with(pdf, old, new):
    pdf.pages = [page.replace(old, new) for page in pdf.pages]
    return pdf


def test_rbc_wrapped_share_class_attaches_to_holding_row():
    # RBC wraps the share-class / security-type text under the holding row
    # and restates the quantity (live shapes: "COM NEW 1,500", bare "2,000").
    # The text belongs to the holding row; the restated number is duplicate
    # evidence kept in raw_line, never a second position.
    pdf = _pages_with(
        load_fixture("rbc/monthly_dual_currency.txt"),
        "ALPHA CORP AAA 10 20.000 200.00 $200.00",
        "ALPHA CORP AAA 10 20.000 200.00 $200.00\nCOM NEW 10\n10",
    )
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    aaa = next(pos for pos in statement.positions if pos.instrument.symbol == "AAA")
    assert aaa.security_description == "COM NEW"
    assert aaa.quantity == 10.0
    assert (aaa.raw_line or "").splitlines() == [
        "ALPHA CORP AAA 10 20.000 200.00 $200.00",
        "COM NEW 10",
        "10",
    ]
    assert [pos.instrument.symbol for pos in statement.positions].count("AAA") == 1
    assert not [row for row in statement.quarantine if "asset-review" in row.reason]


def test_rbc_wrapped_quantity_mismatch_quarantines():
    # A restated number that disagrees with the holding row is real evidence
    # of a misread and must quarantine, never be dropped or attached.
    pdf = _pages_with(
        load_fixture("rbc/monthly_dual_currency.txt"),
        "ALPHA CORP AAA 10 20.000 200.00 $200.00",
        "ALPHA CORP AAA 10 20.000 200.00 $200.00\nCOM 9",
    )
    result = RBCParser().parse(pdf)
    statement = result.statements[0]
    aaa = next(pos for pos in statement.positions if pos.instrument.symbol == "AAA")
    assert aaa.security_description is None
    mismatches = [
        row for row in statement.quarantine
        if row.reason == "continuation quantity does not match the holding row"
    ]
    assert [(row.raw_line, row.reason) for row in mismatches] == [
        ("COM 9", "continuation quantity does not match the holding row"),
    ]


def test_rbc_other_section_wraps_cover_depositary_and_option_underlying():
    # BHP-style depositary shares wrap over two printed lines; option rows
    # carry the underlying issuer name on the next line. Both texts attach to
    # their holding row as security descriptions.
    pdf = _pages_with(
        load_fixture("rbc/monthly_dual_currency.txt"),
        "Other\nCALL .BBB 02/20/26 35 1 2.000 100.00 200.00",
        "Other\n"
        "BHP GROUP LIMITED BHP 5 30.000 150.00 $150.00\n"
        "AMERICAN DEPOSITARY SHARES ON 5\n"
        "ECH RPSNTNG TWO ORD SHS\n"
        "CALL .BBB 02/20/26 35 1 2.000 100.00 200.00\n"
        "MOSAIC COMPANY (THE)",
    )
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    bhp = next(pos for pos in statement.positions if pos.instrument.symbol == "BHP")
    assert bhp.security_description == (
        "AMERICAN DEPOSITARY SHARES ON ECH RPSNTNG TWO ORD SHS"
    )
    assert bhp.quantity == 5.0
    call = next(
        pos for pos in statement.positions if pos.instrument.asset_type == "option"
    )
    assert call.security_description == "MOSAIC COMPANY (THE)"
    assert all("AMERICAN" not in (row.raw_line or "") for row in statement.quarantine)


def test_rbc_page_break_furniture_inside_a_wrap_is_skipped():
    # A wrap that crosses a page break prints the continuation marker and the
    # footnotes block before the wrapped text; furniture is neither attached
    # to the open holding nor quarantined.
    pdf = _pages_with(
        load_fixture("rbc/monthly_dual_currency.txt"),
        "ALPHA CORP AAA 10 20.000 200.00 $200.00",
        "ALPHA CORP AAA 10 20.000 200.00 $200.00\n"
        "-CONTINUEDONNEXTPAGE- FOOTNOTES *- Indicates fully paid\n"
        "COM 10",
    )
    result = RBCParser().parse(pdf)
    statement = result.statements[0]
    aaa = next(pos for pos in statement.positions if pos.instrument.symbol == "AAA")
    assert aaa.security_description == "COM"
    assert not [row for row in statement.quarantine if "asset-review" in row.reason]


def test_rbc_legacy_squeezed_option_rows_and_furniture():
    # 2021-2022 statements lose spaces in text extraction: option verbs print
    # squeezed to their root ("CALLSHOP", "CALL.NTR") and the FX-rate and
    # section-total furniture prints without any spaces. The options still
    # parse; the furniture is skipped, not quarantined.
    pdf = _pages_with(
        load_fixture("rbc/monthly_dual_currency.txt"),
        "Other\nCALL .BBB 02/20/26 35 1 2.000 100.00 200.00",
        "Other\n"
        "(Exchangerate1USD=1.24705CADasofJULY30,2021)\n"
        "CALL.NTR 04/14/22 120 3- 10.450 4,501.30- $3,135.00-\n"
        "CALLSHOP 01/20/23 700 1 138.000 14,661.20 $13,800.00\n"
        "CALLSHOP 01/20/23 70 10 0.600 920.00 ² $600.00\n"
        "TotalValueofOther 4,501.30- $3,135.00-\n"
        "TotalValueofAllSecurities 6,872.44 $5,800.00\n"
        "CALL .BBB 02/20/26 35 1 2.000 100.00 200.00",
    )
    result = RBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    assert statement.quarantine == []
    options = {
        (pos.instrument.option_root, pos.instrument.option_strike): pos
        for pos in statement.positions if pos.instrument.asset_type == "option"
    }
    ntr = options[("NTR", 120.0)]
    assert ntr.quantity == -3.0
    assert ntr.book_value == -4501.30
    shop700 = options[("SHOP", 700.0)]
    assert (shop700.quantity, shop700.market_value) == (1.0, 13800.0)
    shop70 = options[("SHOP", 70.0)]
    assert (shop70.quantity, shop70.market_value) == (10.0, 600.0)


def test_rbc_security_description_persists_to_position_snapshots(tmp_path):
    pdf_text = load_fixture("rbc/monthly_dual_currency.txt")
    pdf = _pages_with(
        load_fixture("rbc/monthly_dual_currency.txt"),
        "ALPHA CORP AAA 10 20.000 200.00 $200.00",
        "ALPHA CORP AAA 10 20.000 200.00 $200.00\nCOM NEW 10",
    )
    result = RBCParser().parse(pdf)
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        source_file_id = _record_source_file(
            conn,
            pdf_text,
            parser_name="rbc",
            parser_version="2.8.0",
            parse_status="ok",
        )
        for statement in result.statements:
            _write_statement(
                conn,
                source_file_id=source_file_id,
                institution_code="RBC_DI",
                stmt=statement,
            )
        rows = conn.execute(
            "SELECT security_description FROM position_snapshots WHERE quantity = 10"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("COM NEW",)]

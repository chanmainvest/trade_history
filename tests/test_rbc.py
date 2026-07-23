"""Self-contained tests for the RBC parser."""
from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
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
    assert next(cash for cash in statement.cash_balances if cash.currency == "CAD").closing_balance == 1055.0

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

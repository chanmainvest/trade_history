from __future__ import annotations

from datetime import date

from trade_history.parsers.common import (
    TextLine,
    extract_account_id_from_text,
    parse_account_ids,
    parse_trade_like_line,
)


def test_trade_line_ignores_running_balance_as_commission() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Nov 20 Sell MICROSOFT CORP -300 416.670 124,987.53 390,405.90",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="TD Webbroker",
        default_year=2024,
    )
    assert issue is None
    assert event is not None
    assert event.side == "SELL"
    assert event.quantity == 300
    assert event.price == 416.67
    assert event.gross_amount == 124987.53
    assert event.commission == 0.0
    assert event.fees == 0.0
    assert event.instrument is None


def test_cash_transfer_line_does_not_become_security_position() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Jan 27 Transfer TRANSFER TO 1234567890 -- -- -$300,000.00",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="CIBC Invest Direct",
        default_year=2023,
    )
    assert issue is None
    assert event is not None
    assert event.event_type == "transfer"
    assert event.side == "TRANSFER_OUT"
    assert event.instrument is None
    assert event.quantity is None
    assert event.gross_amount == -300000.0
    assert event.price is None


def test_interest_line_ignores_institution_words_as_symbol() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Jan 15 Interest CIBC BANK OF CANADA 12.34",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="CIBC Invest Direct",
        default_year=2025,
    )
    assert issue is None
    assert event is not None
    assert event.event_type == "interest"
    assert event.instrument is None


def test_account_id_requires_digits() -> None:
    lines = [
        TextLine(page_number=1, line_number=1, text="ACCOUNT NUMBER THIS"),
        TextLine(page_number=1, line_number=2, text="ACCOUNT NO 1234567890"),
        TextLine(page_number=1, line_number=3, text="Order Execution Only Account 1234 MAIN ST"),
    ]
    account_ids = parse_account_ids(lines, fallback="FALLBACK")
    assert account_ids == ["1234567890"]
    assert extract_account_id_from_text("ACCOUNT NO ACTIVITY") is None
    assert extract_account_id_from_text("Order Execution Only Account 1234 MAIN ST") is None
    assert extract_account_id_from_text("ACCT NUMBER 1234-ABCD") == "1234-ABCD"


def test_td_option_line_parses_strike_and_expiry() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Dec 9 Buy CALL-100 AAPL'25 17JA@225 10 20.450 -20,472.49",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="TD Webbroker",
        default_year=2024,
    )
    assert issue is None
    assert event is not None
    assert event.instrument is not None
    assert event.instrument.asset_type == "option"
    assert event.instrument.option_root == "AAPL"
    assert event.instrument.strike == 225.0
    assert event.instrument.expiry == date(2025, 1, 17)
    assert event.instrument.put_call == "C"


def test_compact_month_code_without_day_uses_monthly_expiry() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Dec 10 Sell PUT -100 MSTR'25 MR@150 -5 5.250 2,608.68",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="TD Webbroker",
        default_year=2024,
    )
    assert issue is None
    assert event is not None
    assert event.instrument is not None
    assert event.instrument.expiry == date(2025, 3, 21)
    assert event.instrument.strike == 150.0
    assert event.instrument.put_call == "P"


def test_cibc_option_line_with_text_expiry_is_parsed() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Jan 21 Sold PUT HL JUN 18 2026 22 -50 3.150 $15,680.55",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="CIBC Invest Direct",
        default_year=2026,
    )
    assert issue is None
    assert event is not None
    assert event.instrument is not None
    assert event.instrument.option_root == "HL"
    assert event.instrument.expiry == date(2026, 6, 18)
    assert event.instrument.strike == 22.0


def test_hsbc_option_line_without_spaces_parses_expiry() -> None:
    line = TextLine(
        page_number=1,
        line_number=1,
        text="Nov27 Sold PUT-100TLT'2616JA@75 (30) 3.250 9,705.54",
    )
    event, issue = parse_trade_like_line(
        account_id="A1",
        text_line=line,
        institution="HSBC direct invest",
        default_year=2023,
    )
    assert issue is None
    assert event is not None
    assert event.instrument is not None
    assert event.instrument.option_root == "TLT"
    assert event.instrument.expiry == date(2026, 1, 16)
    assert event.instrument.strike == 75.0

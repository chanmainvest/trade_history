from __future__ import annotations

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

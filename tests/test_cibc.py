"""Self-contained tests for the CIBC parser."""
from __future__ import annotations

from ledger.parsers.cibc import CIBCParser, _classify_activity
from ledger.parsers.validation import validate_parse_result

from .fixture_loader import load_fixture


def test_cibc_dual_currency_activity_holdings_and_cash():
    pdf = load_fixture("cibc/monthly_dual_currency.txt")
    parser = CIBCParser()
    assert parser.can_handle("CIBC Invest Direct", pdf.pages[0])

    result = parser.parse(pdf)
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.account.account_number == "111-22222"
    assert statement.period_start == "2023-11-01"
    assert statement.period_end == "2023-11-30"
    assert {cash.currency for cash in statement.cash_balances} == {"CAD", "USD"}
    assert {row.txn_type for row in statement.transactions} >= {
        "buy",
        "sell",
        "dividend",
    }
    assert any(
        row.instrument and row.instrument.asset_type == "option"
        for row in statement.transactions
    )
    assert {row.instrument.asset_type for row in statement.positions} >= {
        "equity",
        "mutual_fund",
        "option",
    }
    assert {
        (scope.currency, scope.section_type, scope.completeness)
        for scope in statement.snapshot_sets
    } == {
        ("CAD", "cash", "complete"),
        ("CAD", "positions", "complete"),
        ("USD", "cash", "complete"),
        ("USD", "positions", "complete"),
    }
    assert all(row.source_span and row.source_span.page_number == 1 for row in statement.transactions)
    assert all(row.source_span and row.source_span.page_number == 1 for row in statement.positions)
    assert all(row.source_span and row.source_span.page_number == 1 for row in statement.cash_balances)
    transfers = [row for row in statement.transactions if row.txn_type.startswith("transfer_")]
    account_transfers = [row for row in transfers if row.instrument is None]
    assert [(row.txn_type, row.instrument) for row in account_transfers] == [
        ("transfer_out", None),
        ("transfer_in", None),
    ]
    assert all("REFERENCE" not in (row.description or "") for row in transfers)
    aaa_transfer = next(
        row for row in transfers
        if row.instrument and row.instrument.symbol == "AAA"
    )
    assert aaa_transfer.quantity == 25
    xyz_transfer = next(
        row for row in transfers
        if row.instrument and row.instrument.asset_type == "option"
    )
    assert xyz_transfer.txn_type == "transfer_out"
    assert xyz_transfer.quantity == -3
    assert any(
        item.reason == "unclaimed activity-like row" and "REFERENCE" in item.raw_line
        for item in statement.quarantine
    )
    gamma = next(
        row for row in statement.transactions
        if row.instrument and row.instrument.symbol == "GAM"
    )
    assert gamma.quantity == 12_895.048
    assert gamma.net_amount == -177_441.02
    rio_expiry = next(
        row for row in statement.transactions
        if row.txn_type == "option_expiration"
        and row.instrument
        and row.instrument.symbol == "RIO"
    )
    assert rio_expiry.quantity == 20
    abc_expiry = next(
        row for row in statement.transactions
        if row.txn_type == "option_expiration"
        and row.instrument
        and row.instrument.symbol == "ABC"
    )
    assert abc_expiry.quantity is None
    assert any(
        item.reason == "option event has no printed contract quantity"
        and "CALL .ABC" in item.raw_line
        for item in statement.quarantine
    )
    assert validate_parse_result(result).is_valid


def test_cibc_tfsa_and_option_position():
    result = CIBCParser().parse(load_fixture("cibc/tfsa_option.txt"))
    assert result.errors == []
    statement = result.statements[0]
    assert statement.account.account_number == "333-44444"
    assert statement.account.account_type == "TFSA"
    assert statement.period_end == "2022-08-31"
    option = statement.positions[0].instrument
    assert option.asset_type == "option"
    assert option.option_root == "BCE"
    assert option.option_expiry == "2022-09-16"
    assert option.option_strike == 65.0


def test_cibc_tax_documents_are_explicitly_skipped_not_invalid():
    pdf = load_fixture("cibc/tfsa_option.txt")
    pdf.relpath = "tests/fixtures/cibc/Tax-Document_123.pdf"

    result = CIBCParser().parse(pdf)

    assert result.status == "skipped"
    assert result.skip_reason == "tax document; no brokerage statement extraction"
    assert result.errors == []
    assert result.statements == []


def test_cibc_disclosure_only_page_is_not_part_of_statement():
    pdf = load_fixture("cibc/monthly_dual_currency.txt")
    pdf.pages.append("Disclosures — continued\nGeneral account information only")
    pdf.page_count += 1

    result = CIBCParser().parse(pdf)

    assert result.statements[0].page_numbers == (1,)


def test_cibc_eft_contribution_and_unlabelled_cash_adjustment():
    pdf = load_fixture("cibc/monthly_dual_currency.txt")
    pdf.pages = [
        page.replace(
            "Nov 30 Closing Cash Balance $1,000.00",
            """Nov 20 EFT DEBIT BANK ACCOUNT — — $500,000.00
Nov 21 Contrib TRANSFER TO 9999999999 — — -$14,000.00
Nov 22 GRANITE REAL ESTATE — — -$0.62
Nov 30 Closing Cash Balance $1,000.00""",
        )
        for page in pdf.pages
    ]

    result = CIBCParser().parse(pdf)
    statement = result.statements[0]
    rows = {row.trade_date: row for row in statement.transactions}

    assert rows["2023-11-20"].txn_type == "deposit"
    assert rows["2023-11-20"].net_amount == 500_000.0
    assert rows["2023-11-21"].txn_type == "transfer_out"
    assert rows["2023-11-21"].instrument is None
    assert rows["2023-11-21"].net_amount == -14_000.0
    assert rows["2023-11-22"].txn_type == "adjustment"
    assert rows["2023-11-22"].net_amount == -0.62
    assert validate_parse_result(result).is_valid


def test_cibc_unknown_dated_numeric_activity_marks_cash_scope_incomplete():
    pdf = load_fixture("cibc/monthly_dual_currency.txt")
    pdf.pages = [
        page.replace(
            "Nov 30 Closing Cash Balance $1,000.00",
            "Nov 22 Mystery Event $10.00\nNov 30 Closing Cash Balance $1,000.00",
        )
        for page in pdf.pages
    ]

    result = CIBCParser().parse(pdf)
    statement = result.statements[0]
    cad_cash_scope = next(
        scope
        for scope in statement.snapshot_sets
        if scope.currency == "CAD" and scope.section_type == "cash"
    )

    assert cad_cash_scope.completeness == "unknown"
    assert any("Mystery Event" in row.raw_line for row in statement.quarantine)


def test_cibc_classify_activity_ignores_open_in_equity_issuer_names():
    assert _classify_activity("Bought", "OPEN TEXT CORP 100 45.50 $4,550.00") == "buy"
    assert _classify_activity(
        "Bought",
        "CALL .XYZ DEC 15 2023 100 2 1.500",
    ) == "option_buy_to_close"
    assert _classify_activity(
        "Sold",
        "PUT .ABC JAN 20 2024 50 OPEN CONTRACT 1 2.00",
    ) == "option_sell_to_open"


def test_cibc_corporate_actions_wrapped_names_and_ticker_continuations():
    pdf = load_fixture("cibc/corporate_actions.txt")
    result = CIBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    assert statement.account.account_number == "555-66666"
    assert statement.period_end == "2026-07-31"

    txns = statement.transactions

    # "Shrs in xc" option-contract exchanges: ±qty, blank price/amount cells.
    journals = [row for row in txns if row.txn_type == "journal"]
    assert sorted(row.quantity for row in journals) == [-10.0, 10.0]
    assert all(row.instrument.option_root == "XYZ1" for row in journals)
    assert all(row.net_amount is None for row in journals)
    assert any("SAMPLE DLY SEMICONDUCTOR BR" in row.description for row in journals)
    assert any("SAMPLE DAILY SEMICONDUCTOR" in row.description for row in journals)
    # The digit-bearing corporate-action note lines under the row attach to
    # it: split ratio, and the printed post-event position value.
    out_leg = next(row for row in journals if row.quantity < 0)
    assert "FA 1:10 REV SPLIT & CIL" in out_leg.description
    assert "VALUE $ 2,146.74" in out_leg.description
    in_leg = next(row for row in journals if row.quantity > 0)
    assert "ADJ 1:20 REV SPLIT D:5 SAMPLE" in in_leg.description

    # "Assignment" is CIBC's printed noun form of an assigned option event.
    assigns = [row for row in txns if row.txn_type == "option_assignment"]
    assert len(assigns) == 1
    assert assigns[0].quantity == 2.0
    assert assigns[0].instrument.option_root == "ZYX"
    assert assigns[0].net_amount is None

    # Merger legs print qty, a blank price cell, and a signed cash value.
    mergers = [row for row in txns if row.txn_type == "merger"]
    assert sorted(row.quantity for row in mergers) == [-1500.0, 1500.0]
    assert {row.net_amount for row in mergers} == {4170.0, -4170.0}
    assert all(row.instrument.symbol == "UROY" for row in mergers)
    assert any("SURRENDERED" in row.description for row in mergers)

    buys = [row for row in txns if row.txn_type == "buy"]
    nlr = next(row for row in buys if row.instrument and row.instrument.symbol == "NLR")
    assert nlr.quantity == 100.0
    assert "URANIUM AND NUCLEAR" in nlr.description
    net = next(row for row in buys if row.instrument and row.instrument.symbol == "NET")
    assert net.quantity == 10.0
    sample_buy = next(row for row in buys if "SAMPLE ENERGY" in (row.description or ""))
    assert "COM NEW UNSOLICITED" in sample_buy.description

    # Portfolio rows: a printed (SYM/EXCH) continuation names the holding,
    # and value/par note lines attach too.
    by_symbol = {pos.instrument.symbol: pos for pos in statement.positions}
    assert by_symbol["SEU"].quantity == 1000.0
    assert by_symbol["NET"].quantity == 30.0
    spu = next(
        pos for pos in statement.positions
        if "SAMPLE PHYSICAL URANIUM" in (pos.instrument.name or "")
    )
    assert spu.market_price == 21.5  # the ƒ footnote glyph must not break parsing
    assert "VALUE $0.001 PER SHARE" in spu.instrument.name
    opt = by_symbol["XYZ1"]
    assert opt.instrument.option_root == "XYZ1"  # adjusted roots carry digits

    # The dividend's printed note lines (share count, record/pay dates)
    # attach to the transaction instead of quarantining.
    dividends = [row for row in txns if row.txn_type == "dividend"]
    royal = next(row for row in dividends if "ROYAL SAMPLE" in row.description)
    assert royal.net_amount == 148.20
    for note in ("CASH DIV ON 312 SHS", "REC JUL 02 2026", "PAY JUL 15 2026"):
        assert note in royal.description

    # A cash transfer whose account reference carries a journal suffix
    # (`TRANSFER FROM 555-44444-7`) must not read the suffix as a negative
    # quantity: it is a cash movement, not a position movement.
    cash_xfer = next(
        row for row in txns
        if row.txn_type in {"transfer_in", "transfer_out"}
        and row.net_amount == 2631.60
    )
    assert cash_xfer.quantity is None
    assert cash_xfer.instrument is None
    assert "555-44444-7" in cash_xfer.description

    # An in-kind security transfer carries its source account and printed
    # value as note lines under the row.
    sec_xfer = next(
        row for row in txns
        if "SAMPLE NEVADA CORPORATION" in row.description
    )
    assert sec_xfer.txn_type == "transfer_in"
    assert sec_xfer.quantity == 500.0
    for note in ("TRANSFER FROM 555-44444-7", "VALUE $88,471.95"):
        assert note in sec_xfer.description

    # Cash sections reconcile to the printed closing balances; the CAD-
    # converted presentation total and the note lines are neither recorded
    # as cash nor quarantined.
    assert {c.currency: c.closing_balance for c in statement.cash_balances} == {
        "CAD": 3149.8,
        "USD": 8189.5,
    }
    assert all(
        row.reason != "unrecognized activity row" for row in statement.quarantine
    )
    assert all("FA 1:10" not in row.raw_line for row in statement.quarantine)
    assert all(
        "Total closing" not in row.raw_line for row in statement.quarantine
    )
    # Per-page furniture (period line, previous-statement marker, page footer)
    # is skipped, not quarantined.
    furniture = [
        row for row in statement.quarantine
        if "previous statement" in row.raw_line
        or row.raw_line.startswith("July 1-")
        or "account # 555-66666 page" in row.raw_line
    ]
    assert furniture == []
    # Disclosure/legal footer prose and printer barcodes are skipped too.
    boilerplate = [
        row for row in statement.quarantine
        if "gst/hst" in row.raw_line.lower()
        or "annual fee" in row.raw_line.lower()
        or "conduct of our business" in row.raw_line.lower()
        or "Bay St" in row.raw_line
        or "1-800-" in row.raw_line
        or "HRI-*" in row.raw_line
    ]
    assert boilerplate == []
    scopes = {
        (scope.currency, scope.section_type): scope.completeness
        for scope in statement.snapshot_sets
    }
    assert scopes == {
        ("CAD", "cash"): "complete",
        ("CAD", "positions"): "complete",
        ("USD", "cash"): "complete",
        ("USD", "positions"): "complete",
    }
    assert validate_parse_result(result).is_valid


def test_cibc_residual_flatten_wire_footer_tax_and_option_wraps():
    pdf = load_fixture("cibc/residual_shapes.txt")
    result = CIBCParser().parse(pdf)
    assert result.errors == []
    statement = result.statements[0]
    assert statement.account.account_number == "555-77777"
    txns = statement.transactions

    # The mutual-fund residual flatten ("Shrs in xc 1000THS <FUND> -2 — —")
    # is a journal movement of the fund its description names: the parser
    # keeps the printed-name identity attempt (a synthetic mutual_fund the
    # staged resolver resolves through the reviewed fund lookup).
    flatten = next(row for row in txns if row.txn_type == "journal")
    assert flatten.quantity == -2.0
    assert flatten.instrument is not None
    assert flatten.instrument.asset_type == "mutual_fund"
    assert flatten.instrument.resolution_method == "unresolved_printed_identity"
    assert flatten.net_amount is None
    for fragment in ("1000THS SAMPLE DIVIDEND", "INCOME FUND CL F", "FLATTEN RESIDUAL MFD"):
        assert fragment in flatten.description

    # A dividend whose note line prints a reinvest price with a blank cash
    # cell is an in-kind reinvestment, not a cash dividend.
    drip = next(row for row in txns if row.txn_type == "reinvest_dividend")
    assert drip.quantity == 34.801
    assert drip.net_amount is None
    assert "REINVESTED DIV @ 15.7635" in drip.description

    # Squeezed wire-confirmation footer fragments (wire reference, gross
    # amount / transfer fee) are receipt furniture, not quarantined rows.
    assert all("WIRE=" not in row.raw_line for row in statement.quarantine)
    assert all("GA=" not in row.raw_line for row in statement.quarantine)

    # Option assignments carry their underlying-security CUSIP wraps, split
    # or fused, and the stock buy carries the "ASSIGNMENT OF OPTION" note
    # plus the echoed contract description.
    assigns = [row for row in txns if row.txn_type == "option_assignment"]
    assert [(row.quantity, row.instrument.option_root) for row in assigns] == [
        (2.0, "HLC"),
        (35.0, "HLC"),
    ]
    assert all("A/E 9GDQHF6" in row.description for row in assigns)
    buy = next(
        row for row in txns
        if row.txn_type == "buy" and "SAMPLE MINING COMPANY" in (row.description or "")
    )
    assert "ASSIGNMENT OF OPTION" in buy.description
    assert "PUT HLC JUN 18 2026 22" in buy.description

    # The fixed NON-RES TAX WITHHELD label is a cash event with no security
    # identity — recorded without an unresolved-identity marker.
    tax = next(row for row in txns if row.txn_type == "tax_withholding")
    assert tax.net_amount == -22.23
    assert tax.instrument is None
    assert tax.resolution_method is None

    # No activity row was quarantined: the cash scopes stay authoritative.
    assert statement.quarantine == []
    scopes = {
        (scope.currency, scope.section_type): scope.completeness
        for scope in statement.snapshot_sets
    }
    assert scopes == {
        ("CAD", "cash"): "complete",
        ("USD", "cash"): "complete",
    }
    assert validate_parse_result(result).is_valid

"""Tests for RBC parser."""
from ledger.parsers.rbc import RBCParser

from .test_cibc import _load


def test_rbc_dual_currency_split():
    pdf = _load("RBC Invest Direct/Statement-8036 2026-01-30.txt")
    res = RBCParser().parse(pdf)
    assert res.errors == []
    assert len(res.statements) == 2
    ccys = sorted(s.account.base_currency for s in res.statements)
    assert ccys == ["CAD", "USD"]
    for s in res.statements:
        assert s.account.account_number == "669-28036-2-7"
        assert s.period_start == "2026-01-01"
        assert s.period_end == "2026-01-30"


def test_rbc_holdings_and_dividend():
    pdf = _load("RBC Invest Direct/Statement-8036 2026-01-30.txt")
    res = RBCParser().parse(pdf)
    cad = next(s for s in res.statements if s.account.base_currency == "CAD")
    # Common shares + Mutual funds + Foreign Securities = 7 holdings
    assert len(cad.positions) == 7
    asset_types = {p.instrument.asset_type for p in cad.positions}
    assert "equity" in asset_types
    assert "mutual_fund" in asset_types
    # Dividend transaction present
    assert any(t.txn_type == "dividend" and t.net_amount == 1175.0
               for t in cad.transactions)
    assert cad.cash_balances[0].closing_balance == 3169.56


def test_rbc_options_classification():
    pdf = _load("RBC Invest Direct/67027469-2024Aug30-2024Aug30.txt")
    res = RBCParser().parse(pdf)
    usd = next(s for s in res.statements if s.account.base_currency == "USD")
    opt_txns = [t for t in usd.transactions
                if t.instrument and t.instrument.asset_type == "option"]
    assert len(opt_txns) >= 4
    # Each option txn must carry strike + expiry + type
    for t in opt_txns:
        assert t.instrument.option_strike is not None
        assert t.instrument.option_expiry is not None
        assert t.instrument.option_type in {"CALL", "PUT"}
        assert t.txn_type.startswith("option_")


def test_rbc_annual_performance_report_parses_money_weighted_returns():
    pdf = _load("RBC Invest Direct/66844715-2022_annual_report.txt")
    res = RBCParser().parse(pdf)
    assert res.errors == []
    assert len(res.statements) == 1
    statement = res.statements[0]
    assert statement.statement_type == "annual"
    assert statement.period_start == "2022-01-01"
    assert statement.period_end == "2022-12-31"
    rows = {row.currency: row for row in statement.annual_performance}
    assert rows["CAD"].ending_market_value == 504398.5
    assert rows["CAD"].money_weighted_1y == -10.63
    assert rows["CAD"].money_weighted_since == 5.99
    assert rows["USD"].since_date == "2022-03-28"
    assert rows["USD"].beginning_market_value == 0.0
    assert rows["USD"].deposits_transfers_in == 70214.83
    assert rows["USD"].withdrawals_transfers_out == -209.09
    assert rows["USD"].net_investment_return == -22423.89
    assert rows["USD"].ending_market_value == 47581.85
    assert rows["USD"].money_weighted_since == -48.03

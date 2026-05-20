"""Tests for TD parser."""
from ledger.parsers.td import TDParser

from .test_cibc import _load


def test_td_dual_account_split():
    pdf = _load("TD Webbroker/Statement_58MRB0_2025-10.txt")
    res = TDParser().parse(pdf)
    assert res.errors == []
    accts = sorted(s.account.account_number for s in res.statements)
    assert accts == ["58MRB0-CAD", "58MRB0-USD"]
    for s in res.statements:
        assert s.period_start == "2025-10-01"
        assert s.period_end == "2025-10-31"


def test_td_holdings_reconcile():
    pdf = _load("TD Webbroker/Statement_58MRB0_2025-12.txt")
    res = TDParser().parse(pdf)
    usd = next(s for s in res.statements if s.account.base_currency == "USD")
    eq_mv = sum(p.market_value or 0 for p in usd.positions
                if p.instrument.asset_type == "equity")
    opt_mv = sum(p.market_value or 0 for p in usd.positions
                 if p.instrument.asset_type == "option")
    cash_close = usd.cash_balances[0].closing_balance
    # Total Portfolio per PDF = $1,915,163.16
    total = eq_mv + opt_mv + cash_close
    assert abs(total - 1_915_163.16) < 1.0, (eq_mv, opt_mv, cash_close, total)
    # No quarantine for this statement
    assert usd.quarantine == []


def test_td_option_activity_classification():
    pdf = _load("TD Webbroker/Statement_58MRB0_2025-12.txt")
    res = TDParser().parse(pdf)
    usd = next(s for s in res.statements if s.account.base_currency == "USD")
    opt_txns = [t for t in usd.transactions
                if t.instrument and t.instrument.asset_type == "option"]
    assert opt_txns, "no option txns parsed"
    for t in opt_txns:
        assert t.instrument.option_strike is not None
        assert t.instrument.option_expiry is not None
        assert t.instrument.option_multiplier == 100
        assert t.txn_type.startswith("option_")


def test_td_summary_pdf():
    pdf = _load("TD Webbroker/Statement_58MRB0_2023_summary.txt")
    res = TDParser().parse(pdf)
    assert len(res.statements) == 1
    s = res.statements[0]
    assert s.statement_type == "annual"
    assert s.period_start == "2023-01-01"
    assert s.period_end == "2023-12-31"


def test_td_legacy_quarterly_file_splits_bundled_months():
    pdf = _load("TD Webbroker/Statement_77FF49_2016_01-04.txt")
    res = TDParser().parse(pdf)
    assert res.errors == []
    assert len(res.statements) == 8
    periods = sorted({(s.period_start, s.period_end) for s in res.statements})
    assert periods == [
        ("2016-01-01", "2016-01-31"),
        ("2016-02-01", "2016-02-29"),
        ("2016-03-01", "2016-03-31"),
        ("2016-04-01", "2016-04-30"),
    ]
    assert {s.account.account_number for s in res.statements} == {"77FF49-CAD", "77FF49-USD"}
    assert all(s.positions for s in res.statements)
    assert all(s.cash_balances for s in res.statements)

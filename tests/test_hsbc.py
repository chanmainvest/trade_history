"""Tests for HSBC parser."""
from ledger.parsers.hsbc import HSBCParser

from .test_cibc import _load


def test_hsbc_two_account_split():
    pdf = _load("HSBC direct invest/2023-09.txt")
    res = HSBCParser().parse(pdf)
    assert res.errors == []
    assert len(res.statements) == 2
    ccys = sorted(s.account.base_currency for s in res.statements)
    assert ccys == ["CAD", "USD"]


def test_hsbc_period_and_cash():
    pdf = _load("HSBC direct invest/2023-09.txt")
    res = HSBCParser().parse(pdf)
    for s in res.statements:
        assert s.cash_balances
        assert s.period_start.startswith("2023-09")
        assert s.period_end.startswith("2023-09")

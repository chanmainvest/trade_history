"""Tests for ExtractorRegistry (no PDF required — tests can_handle logic)."""


import trade_history.extractors  # noqa: F401 — triggers all registrations
from trade_history.extractors.registry import ExtractorRegistry


def test_registry_has_extractors():
    extractors = ExtractorRegistry.list_extractors()
    assert len(extractors) >= 5


def test_cibc_investors_edge_can_handle():
    from pathlib import Path

    from trade_history.extractors.cibc.investors_edge import CIBCInvestorsEdge

    assert CIBCInvestorsEdge.can_handle(
        Path("dummy.pdf"),
        "Investor's Edge Investment Account Statement"
    )
    assert CIBCInvestorsEdge.can_handle(
        Path("dummy.pdf"),
        "Self-Directed Tax Free Savings Account"
    )
    assert not CIBCInvestorsEdge.can_handle(
        Path("dummy.pdf"),
        "Imperial Investor Service"
    )


def test_cibc_imperial_can_handle():
    from pathlib import Path

    from trade_history.extractors.cibc.imperial_service import CIBCImperialService

    assert CIBCImperialService.can_handle(
        Path("dummy.pdf"),
        "Imperial Investor Service Statement"
    )


def test_hsbc_can_handle():
    from pathlib import Path

    from trade_history.extractors.hsbc.investdirect import HSBCInvestDirect

    assert HSBCInvestDirect.can_handle(
        Path("dummy.pdf"),
        "HSBC InvestDirect Account Statement"
    )
    # Fee file
    assert HSBCInvestDirect.can_handle(
        Path("hsbc_2024_fees.pdf"),
        "Some content"
    )


def test_rbc_can_handle():
    from pathlib import Path

    from trade_history.extractors.rbc.direct_investing import RBCDirectInvesting

    assert RBCDirectInvesting.can_handle(
        Path("dummy.pdf"),
        "RBC Direct Investing Statement"
    )


def test_td_can_handle():
    from pathlib import Path

    from trade_history.extractors.td.webbroker import TDWebbroker

    assert TDWebbroker.can_handle(
        Path("dummy.pdf"),
        "TD Direct Investing Statement Period"
    )

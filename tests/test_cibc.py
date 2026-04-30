"""Smoke tests for the CIBC parser using real text dumps."""
from __future__ import annotations

from pathlib import Path

from ledger.parsers.cibc import CIBCParser
from ledger.pdf_text import PdfText

DUMPS = Path(__file__).resolve().parents[1] / "data" / "text_dumps"


def _load(relpath: str) -> PdfText:
    p = DUMPS / relpath
    text = p.read_text(encoding="utf-8")
    pages = text.split("----- PAGE BREAK -----")
    # strip leading metadata comment lines
    stripped = []
    for i, pg in enumerate(pages):
        lines = pg.splitlines()
        if i == 0:
            lines = [ln for ln in lines if not ln.startswith("# ")]
        stripped.append("\n".join(lines))
    return PdfText(
        relpath=str(p), page_count=len(stripped), pages=stripped,
        sha256="x", size_bytes=len(text),
    )


def test_can_handle_imperial_service():
    pdf = _load("CIBC Imperial Service/2019_12_eStatements.txt")
    p = CIBCParser()
    assert p.can_handle("CIBC Imperial Service", pdf.pages[0])


def test_parse_imperial_service_2019_12():
    pdf = _load("CIBC Imperial Service/2019_12_eStatements.txt")
    res = CIBCParser().parse(pdf)
    assert res.statements, res.errors
    s = res.statements[0]
    assert s.account.account_number == "586-33338"
    assert s.period_start == "2019-12-01"
    assert s.period_end == "2019-12-31"
    # Mutual fund dividends present
    div_rows = [t for t in s.transactions if t.txn_type == "dividend"]
    assert len(div_rows) >= 3
    # Cash balance
    assert any(c.currency == "CAD" for c in s.cash_balances)
    # Mutual fund positions
    mf_pos = [p for p in s.positions if p.instrument.asset_type == "mutual_fund"]
    assert len(mf_pos) >= 3


def test_parse_investors_edge_2023_11():
    pdf = _load("CIBC Invest Direct/2023_11_eStatements.txt")
    res = CIBCParser().parse(pdf)
    assert res.statements, res.errors
    s = res.statements[0]
    assert s.account.account_number == "588-93738"
    assert s.period_end == "2023-11-30"
    # Should have option txns (Bought/Sold CALL/PUT)
    opt_txns = [t for t in s.transactions
                if t.instrument and t.instrument.asset_type == "option"]
    assert opt_txns, "expected option transactions"
    # Should have stock buys/sells
    eq_txns = [t for t in s.transactions if t.txn_type in {"buy", "sell"}
               and t.instrument and t.instrument.asset_type != "option"]
    assert eq_txns
    # Cash balances present for both currencies
    ccs = {c.currency for c in s.cash_balances}
    assert "CAD" in ccs and "USD" in ccs
    # Positions: equity + mutual_fund + option
    asset_types = {p.instrument.asset_type for p in s.positions}
    assert {"equity", "mutual_fund", "option"} <= asset_types or \
           {"etf", "mutual_fund", "option"} <= asset_types


def test_parse_tfsa_2022_08():
    pdf = _load("CIBC TSFA/2022_08_eStatements.txt")
    res = CIBCParser().parse(pdf)
    assert res.statements, res.errors
    s = res.statements[0]
    assert s.account.account_number == "605-82155"
    assert s.account.account_type == "TFSA"
    assert s.period_end == "2022-08-31"
    # Has at least one option position (BCE call)
    opt_pos = [p for p in s.positions if p.instrument.asset_type == "option"]
    assert opt_pos

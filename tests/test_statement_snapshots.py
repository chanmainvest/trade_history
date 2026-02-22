from __future__ import annotations

from trade_history.parsers.common import TextLine, extract_statement_snapshots


def test_extract_statement_snapshots_parses_common_metrics() -> None:
    lines = [
        TextLine(page_number=1, line_number=1, text="Jan 1 Opening cash balance $100.00"),
        TextLine(page_number=1, line_number=2, text="Jan 31 Closing cash balance $250.00"),
        TextLine(page_number=1, line_number=3, text="Total Portfolio $900.00 $1,000.00"),
    ]
    snapshots = extract_statement_snapshots(lines, default_account_id="A1", default_year=2024)
    metrics = {(s.metric_code, s.value_native) for s in snapshots}

    assert ("cash_opening", 100.0) in metrics
    assert ("cash_closing", 250.0) in metrics
    assert ("portfolio_total", 1000.0) in metrics


def test_extract_statement_snapshots_uses_latest_account_context() -> None:
    lines = [
        TextLine(page_number=1, line_number=1, text="ACCOUNT NO 111111"),
        TextLine(page_number=1, line_number=2, text="Jan 31 Closing cash balance $50.00"),
        TextLine(page_number=1, line_number=3, text="ACCOUNT NO 222222"),
        TextLine(page_number=1, line_number=4, text="Jan 31 Closing cash balance $75.00"),
    ]
    snapshots = extract_statement_snapshots(lines, default_account_id="FALLBACK", default_year=2024)
    rows = {(s.account_id, s.metric_code, s.value_native) for s in snapshots}

    assert ("111111", "cash_closing", 50.0) in rows
    assert ("222222", "cash_closing", 75.0) in rows


def test_extract_statement_snapshots_ignores_percentage_tail_values() -> None:
    lines = [
        TextLine(
            page_number=1,
            line_number=1,
            text="Total Portfolio $25,442.00 $3,872.00 100.00%",
        )
    ]
    snapshots = extract_statement_snapshots(lines, default_account_id="A1", default_year=2024)
    assert len(snapshots) == 1
    assert snapshots[0].metric_code == "portfolio_total"
    assert snapshots[0].value_native == 3872.0

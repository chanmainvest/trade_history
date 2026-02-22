from __future__ import annotations

from pathlib import Path

from trade_history.config import settings
from trade_history.db.sqlite import get_connection, init_db
from trade_history.services.analytics import monthly_reconciliation_snapshot_lines


def test_monthly_reconciliation_snapshot_lines_returns_source_metadata() -> None:
    original_sqlite_path = settings.sqlite_path
    test_path = Path("data/test_reconciliation.sqlite")
    if test_path.exists():
        test_path.unlink()
    settings.sqlite_path = test_path
    try:
        init_db()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO accounts(account_id, institution, account_name)
                VALUES ('A1', 'Broker X', 'Account 1')
                """
            )
            conn.execute(
                """
                INSERT INTO statement_files(
                  institution, account_id, file_path, period_start, period_end,
                  format_version, parse_status, checksum
                ) VALUES (
                  'Broker X', 'A1', 'Statements/BrokerX/2024-01.pdf', '2024-01-01', '2024-01-31',
                  'test', 'success', 'abc'
                )
                """
            )
            source_file_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO statement_snapshots(
                  source_file_id, account_id, snapshot_date, metric_code, currency,
                  value_native, source_line_ref, raw_line
                ) VALUES (?, 'A1', '2024-01-31', 'cash_closing', 'USD', 100.0, 'p1:l9', 'Jan 31 Closing cash balance $100.00')
                """,
                (source_file_id,),
            )
            conn.commit()

        result = monthly_reconciliation_snapshot_lines(
            month="2024-01",
            account_id="A1",
            currency_native="USD",
            display_currency="CAD",
            institution="Broker X",
        )
        assert result["display_currency"] == "CAD"
        assert len(result["items"]) == 1
        row = result["items"][0]
        assert row["metric_code"] == "cash_closing"
        assert row["file_name"] == "2024-01.pdf"
        # No fx table was loaded in test; fallback USD/CAD 1.35 is used.
        assert round(float(row["value_display"]), 2) == 135.0
    finally:
        settings.sqlite_path = original_sqlite_path

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from trade_history.config import settings
from trade_history.db.sqlite import db_session, init_db
from trade_history.ingest.utils import file_checksum
from trade_history.parsers.base import ParsedAccount, ParsedEvent, ParsedInstrument, ParsedSnapshot, ParsedStatement
from trade_history.parsers.registry import PARSER_BY_FOLDER, parser_for_path


@dataclass(slots=True)
class StatementIngestReport:
    total_files: int = 0
    parsed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    events_inserted: int = 0
    issues_logged: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_files": self.total_files,
            "parsed_files": self.parsed_files,
            "skipped_files": self.skipped_files,
            "failed_files": self.failed_files,
            "events_inserted": self.events_inserted,
            "issues_logged": self.issues_logged,
        }


def discover_statement_files(root: Path, institutions: set[str] | None = None) -> list[Path]:
    candidates: list[Path] = []
    institution_keys = {name.lower() for name in institutions} if institutions else None
    for file_path in sorted(root.rglob("*.pdf")):
        parent = file_path.parent.name.lower()
        if parent not in PARSER_BY_FOLDER:
            continue
        if institution_keys and parent not in institution_keys:
            continue
        candidates.append(file_path)
    return candidates


def _mask_account(account_id: str) -> str:
    if len(account_id) <= 4:
        return account_id
    return f"{'*' * (len(account_id) - 4)}{account_id[-4:]}"


def _upsert_account(conn: sqlite3.Connection, account: ParsedAccount) -> None:
    conn.execute(
        """
        INSERT INTO accounts(account_id, institution, account_name, account_type, base_currency, masked_number)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
          institution = excluded.institution,
          account_name = COALESCE(excluded.account_name, accounts.account_name),
          account_type = COALESCE(excluded.account_type, accounts.account_type),
          base_currency = COALESCE(excluded.base_currency, accounts.base_currency),
          masked_number = COALESCE(excluded.masked_number, accounts.masked_number)
        """,
        (
            account.account_id,
            account.institution,
            account.account_name,
            account.account_type,
            account.base_currency,
            account.masked_number or _mask_account(account.account_id),
        ),
    )


def _upsert_instrument(conn: sqlite3.Connection, instrument: ParsedInstrument | None) -> int | None:
    if instrument is None:
        return None
    expiry = instrument.expiry.isoformat() if instrument.expiry else None
    cursor = conn.execute(
        """
        INSERT INTO instruments (
          symbol_raw, symbol_norm, asset_type, option_root, strike, expiry,
          put_call, multiplier, exchange, sector
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol_norm, asset_type, IFNULL(expiry, ''), IFNULL(strike, -1), IFNULL(put_call, ''))
        DO UPDATE SET
          symbol_raw = excluded.symbol_raw,
          option_root = COALESCE(excluded.option_root, instruments.option_root),
          strike = COALESCE(excluded.strike, instruments.strike),
          expiry = COALESCE(excluded.expiry, instruments.expiry),
          put_call = COALESCE(excluded.put_call, instruments.put_call),
          multiplier = COALESCE(excluded.multiplier, instruments.multiplier),
          exchange = COALESCE(excluded.exchange, instruments.exchange),
          sector = COALESCE(excluded.sector, instruments.sector)
        RETURNING instrument_id
        """,
        (
            instrument.symbol_raw,
            instrument.symbol_norm,
            instrument.asset_type,
            instrument.option_root,
            instrument.strike,
            expiry,
            instrument.put_call,
            instrument.multiplier,
            instrument.exchange,
            instrument.sector,
        ),
    )
    row = cursor.fetchone()
    return int(row["instrument_id"]) if row else None


def _insert_event(conn: sqlite3.Connection, event: ParsedEvent, source_file_id: int, instrument_id: int | None) -> None:
    conn.execute(
        """
        INSERT INTO events(
          account_id, trade_date, settle_date, event_type, instrument_id, side, quantity, price,
          gross_amount, commission, fees, currency, source_file_id, source_line_ref, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.account_id,
            event.trade_date.isoformat(),
            event.settle_date.isoformat() if event.settle_date else None,
            event.event_type,
            instrument_id,
            event.side,
            event.quantity,
            event.price,
            event.gross_amount,
            event.commission,
            event.fees,
            event.currency,
            source_file_id,
            event.source_line_ref,
            event.notes,
        ),
    )


def _insert_snapshot(conn: sqlite3.Connection, snapshot: ParsedSnapshot, source_file_id: int) -> None:
    conn.execute(
        """
        INSERT INTO statement_snapshots(
          source_file_id, account_id, snapshot_date, metric_code, currency,
          value_native, source_line_ref, raw_line
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_file_id,
            snapshot.account_id,
            snapshot.snapshot_date.isoformat() if snapshot.snapshot_date else None,
            snapshot.metric_code,
            snapshot.currency,
            snapshot.value_native,
            snapshot.source_line_ref,
            snapshot.raw_line,
        ),
    )


def _upsert_statement_file(
    conn: sqlite3.Connection,
    parsed: ParsedStatement,
    checksum: str,
    parse_status: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO statement_files(
          institution, account_id, file_path, period_start, period_end, format_version,
          parse_status, parse_message, checksum
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
          institution = excluded.institution,
          account_id = excluded.account_id,
          period_start = excluded.period_start,
          period_end = excluded.period_end,
          format_version = excluded.format_version,
          parse_status = excluded.parse_status,
          parse_message = excluded.parse_message,
          checksum = excluded.checksum,
          updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (
            parsed.institution,
            parsed.accounts[0].account_id if parsed.accounts else None,
            str(parsed.file_path),
            parsed.period_start.isoformat() if parsed.period_start else None,
            parsed.period_end.isoformat() if parsed.period_end else None,
            parsed.format_version,
            parse_status,
            parsed.parse_message,
            checksum,
        ),
    )
    row = cursor.fetchone()
    return int(row["id"]) if row else 0


def _should_skip(conn: sqlite3.Connection, file_path: Path, checksum: str) -> bool:
    row = conn.execute(
        "SELECT checksum FROM statement_files WHERE file_path = ?",
        (str(file_path),),
    ).fetchone()
    if row is None:
        return False
    return row["checksum"] == checksum


def _clear_existing_events_for_file(conn: sqlite3.Connection, source_file_id: int) -> None:
    conn.execute(
        """
        DELETE FROM lot_closures
        WHERE close_event_id IN (
          SELECT event_id FROM events WHERE source_file_id = ?
        )
        """,
        (source_file_id,),
    )
    conn.execute(
        """
        DELETE FROM transfers
        WHERE from_event_id IN (SELECT event_id FROM events WHERE source_file_id = ?)
           OR to_event_id IN (SELECT event_id FROM events WHERE source_file_id = ?)
        """,
        (source_file_id, source_file_id),
    )
    conn.execute("DELETE FROM events WHERE source_file_id = ?", (source_file_id,))
    conn.execute("DELETE FROM statement_snapshots WHERE source_file_id = ?", (source_file_id,))
    conn.execute(
        "DELETE FROM quarantine_transactions WHERE file_path = (SELECT file_path FROM statement_files WHERE id = ?)",
        (source_file_id,),
    )


def _normalize_institutions(institutions: Iterable[str] | None) -> set[str] | None:
    if institutions is None:
        return None
    normalized = {item.strip().lower() for item in institutions if item.strip()}
    return normalized or None


def ingest_statements(
    root: Path | None = None,
    institutions: Iterable[str] | None = None,
    force: bool = False,
) -> StatementIngestReport:
    init_db()
    report = StatementIngestReport()
    source_root = root or settings.statements_root
    include = _normalize_institutions(institutions)
    files = discover_statement_files(source_root, include)
    report.total_files = len(files)

    with db_session() as conn:
        conn.execute(
            "INSERT INTO job_runs(job_name, status, details_json) VALUES(?, ?, ?)",
            ("ingest_statements", "running", "{}"),
        )
        job_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        for file_path in files:
            parser = parser_for_path(file_path)
            if parser is None:
                report.skipped_files += 1
                continue

            checksum = file_checksum(file_path)
            if not force and _should_skip(conn, file_path, checksum):
                report.skipped_files += 1
                continue

            try:
                parsed = parser.parse(file_path)
                status = "warning" if parsed.issues or not parsed.events else "success"
                source_file_id = _upsert_statement_file(conn, parsed, checksum, status)
                if source_file_id:
                    _clear_existing_events_for_file(conn, source_file_id)

                for account in parsed.accounts:
                    _upsert_account(conn, account)

                for event in parsed.events:
                    instrument_id = _upsert_instrument(conn, event.instrument)
                    _insert_event(conn, event, source_file_id, instrument_id)
                    report.events_inserted += 1

                for snapshot in parsed.snapshots:
                    _insert_snapshot(conn, snapshot, source_file_id)

                for issue in parsed.issues:
                    conn.execute(
                        """
                        INSERT INTO quarantine_transactions(institution, file_path, page_number, raw_line, reason)
                        VALUES(?, ?, ?, ?, ?)
                        """,
                        (
                            parsed.institution,
                            str(file_path),
                            issue.page_number,
                            issue.raw_line,
                            issue.reason,
                        ),
                    )
                    report.issues_logged += 1

                report.parsed_files += 1
            except Exception as exc:
                report.failed_files += 1
                fallback = ParsedStatement(
                    institution=file_path.parent.name,
                    file_path=file_path,
                    format_version="failed",
                    parse_message=str(exc),
                )
                _upsert_statement_file(conn, fallback, checksum, "failed")

        conn.execute(
            """
            UPDATE job_runs
            SET status = ?, finished_at = CURRENT_TIMESTAMP, details_json = ?
            WHERE id = ?
            """,
            ("success", str(report.to_dict()), job_id),
        )

    return report

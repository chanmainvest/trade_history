"""Small schema-v6 seed helpers for database-facing tests."""
from __future__ import annotations

import hashlib

from ledger.db import sqlite as sqlite_db
from ledger.identity import canonical_statement_key, evidence_occurrence
from ledger.parsers.types import PARSER_CONTRACT_VERSION


def seed_source(conn, relpath: str) -> int:
    digest = hashlib.sha256(relpath.encode()).hexdigest()
    source_file_id = conn.execute(
        """
        INSERT INTO source_files(relpath, sha256, parse_status)
        VALUES (?, ?, 'ok')
        RETURNING source_file_id
        """,
        (relpath, digest),
    ).fetchone()[0]
    sqlite_db.record_ingestion_run(
        conn,
        source_file_id=source_file_id,
        source_sha256=digest,
        parser_name="synthetic-test",
        parser_version="1",
        contract_version=PARSER_CONTRACT_VERSION,
        status="ok",
    )
    return source_file_id


def seed_statement(
    conn,
    *,
    account_id: int,
    source_file_id: int,
    period_end: str,
    period_start: str | None = None,
    statement_type: str = "monthly",
) -> int:
    period_start = period_start or period_end[:8] + "01"
    row = conn.execute(
        """
        SELECT sf.sha256, sf.relpath, sf.active_ingestion_run_id,
               i.code AS institution_code, a.account_number
          FROM source_files sf
          JOIN accounts a ON a.account_id = ?
          JOIN institutions i ON i.institution_id = a.institution_id
         WHERE sf.source_file_id = ?
        """,
        (account_id, source_file_id),
    ).fetchone()
    key = canonical_statement_key(
        source_identity=row["sha256"] or row["relpath"],
        institution_code=row["institution_code"],
        account_number=row["account_number"],
        period_start=period_start,
        period_end=period_end,
        statement_type=statement_type,
    )
    return conn.execute(
        """
        INSERT INTO statements(
            source_file_id, ingestion_run_id, account_id, statement_key,
            period_start, period_end, statement_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING statement_id
        """,
        (
            source_file_id,
            row["active_ingestion_run_id"],
            account_id,
            key,
            period_start,
            period_end,
            statement_type,
        ),
    ).fetchone()[0]


def _statement_context(conn, statement_id: int):
    return conn.execute(
        """
        SELECT s.statement_key, s.source_file_id, s.ingestion_run_id,
               s.account_id, s.period_end, ir.parser_version
          FROM statements s
          JOIN ingestion_runs ir ON ir.ingestion_run_id = s.ingestion_run_id
         WHERE s.statement_id = ?
        """,
        (statement_id,),
    ).fetchone()


def seed_snapshot_set(
    conn,
    *,
    statement_id: int,
    currency: str,
    section_type: str,
    completeness: str = "complete",
) -> int:
    context = _statement_context(conn, statement_id)
    return sqlite_db.upsert_snapshot_set(
        conn,
        statement_id=statement_id,
        account_id=context["account_id"],
        as_of_date=context["period_end"],
        currency=currency,
        section_type=section_type,
        scope_key="default",
        completeness=completeness,
        evidence_id=None,
        reported_total=None,
        validation_status="valid",
    )


def _seed_evidence(conn, *, statement_id: int, row_kind: str, row_index: int) -> int:
    context = _statement_context(conn, statement_id)
    occurrence = evidence_occurrence(context["statement_key"], row_kind, row_index)
    return sqlite_db.upsert_source_evidence(
        conn,
        source_file_id=context["source_file_id"],
        ingestion_run_id=context["ingestion_run_id"],
        row_kind=row_kind,
        occurrence=occurrence,
        raw_text=f"synthetic {row_kind} evidence",
        parser_version=context["parser_version"],
        parser_rule="test:seed",
    )


def seed_position(
    conn,
    *,
    statement_id: int,
    instrument_id: int,
    quantity: float,
    currency: str,
    market_value: float | None = None,
    completeness: str = "complete",
) -> int:
    context = _statement_context(conn, statement_id)
    snapshot_set_id = seed_snapshot_set(
        conn,
        statement_id=statement_id,
        currency=currency,
        section_type="positions",
        completeness=completeness,
    )
    evidence_id = _seed_evidence(
        conn,
        statement_id=statement_id,
        row_kind="position",
        row_index=instrument_id,
    )
    return conn.execute(
        """
        INSERT INTO position_snapshots(
            statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
            instrument_id, quantity, market_value, currency, raw_line
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic position evidence')
        RETURNING snapshot_id
        """,
        (
            statement_id,
            snapshot_set_id,
            evidence_id,
            context["account_id"],
            context["period_end"],
            instrument_id,
            quantity,
            market_value,
            currency,
        ),
    ).fetchone()[0]


def seed_cash(
    conn,
    *,
    statement_id: int,
    currency: str,
    closing_balance: float,
    opening_balance: float | None = None,
    completeness: str = "complete",
) -> int:
    context = _statement_context(conn, statement_id)
    snapshot_set_id = seed_snapshot_set(
        conn,
        statement_id=statement_id,
        currency=currency,
        section_type="cash",
        completeness=completeness,
    )
    row_index = int.from_bytes(currency.encode(), "big")
    evidence_id = _seed_evidence(
        conn,
        statement_id=statement_id,
        row_kind="cash",
        row_index=row_index,
    )
    return conn.execute(
        """
        INSERT INTO cash_balances(
            statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
            currency, opening_balance, closing_balance, raw_line
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'synthetic cash evidence')
        RETURNING cash_balance_id
        """,
        (
            statement_id,
            snapshot_set_id,
            evidence_id,
            context["account_id"],
            context["period_end"],
            currency,
            opening_balance,
            closing_balance,
        ),
    ).fetchone()[0]

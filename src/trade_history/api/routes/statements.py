"""GET /statements — statement registry browsing and PDF serving."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from trade_history.api.deps import get_sqlite

router = APIRouter()


@router.get("/accounts")
def list_accounts_with_statements(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """List accounts that have at least one processed statement."""
    rows = conn.execute(
        """
        SELECT
            sr.institution,
            sr.account_id,
            sr.institution || ' | ' || sr.account_id AS group_key,
            COUNT(*) as statement_count
        FROM statement_registry sr
        WHERE sr.status IN ('ok', 'partial')
          AND sr.account_id IS NOT NULL
        GROUP BY sr.institution, sr.account_id
        ORDER BY sr.institution, sr.account_id
        """,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/by-source-file")
def get_statement_by_source_file(
    source_file: str,
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Look up a statement by its source_file path."""
    row = conn.execute(
        """
        SELECT id, institution, account_id, period_start, period_end, status
        FROM statement_registry
        WHERE source_file = ?
        """,
        (source_file,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Statement not found for this source file")
    return dict(row)


@router.get("")
def list_statements(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
    institution: str | None = None,
    account_id: str | None = None,
    status: str | None = None,
):
    """List statements, optionally filtered by institution/account/status."""
    filters = []
    params: list = []

    if institution:
        filters.append("sr.institution = ?")
        params.append(institution)
    if account_id:
        filters.append("sr.account_id = ?")
        params.append(account_id)
    if status:
        filters.append("sr.status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = conn.execute(
        f"""
        SELECT
            sr.id,
            sr.source_file,
            sr.institution,
            sr.account_id,
            sr.period_start,
            sr.period_end,
            sr.status,
            sr.transaction_count,
            sr.balance_validated,
            (sr.docling_json IS NOT NULL) as has_docling_json
        FROM statement_registry sr
        {where}
        ORDER BY sr.period_end DESC, sr.institution, sr.account_id
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{statement_id}/transactions")
def get_statement_transactions(
    statement_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Return transactions linked to a specific statement."""
    rows = conn.execute(
        """
        SELECT
            t.id,
            t.trade_date,
            t.activity,
            i.symbol,
            i.asset_type,
            t.quantity,
            t.price,
            t.amount,
            t.currency,
            t.raw_text,
            t.docling_ref,
            t.docling_page
        FROM transactions t
        LEFT JOIN instruments i ON i.id = t.instrument_id
        WHERE t.statement_id = ?
        ORDER BY t.trade_date, t.id
        """,
        (statement_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Parse docling_ref: stored as JSON array string, or legacy single ref
        raw_ref = d.get("docling_ref")
        if raw_ref:
            try:
                parsed = json.loads(raw_ref)
                if isinstance(parsed, list):
                    d["docling_refs"] = parsed
                else:
                    d["docling_refs"] = [raw_ref]
            except (json.JSONDecodeError, TypeError):
                d["docling_refs"] = [raw_ref]
        else:
            d["docling_refs"] = []
        del d["docling_ref"]
        result.append(d)
    return result


@router.get("/{statement_id}/holdings")
def get_statement_holdings(
    statement_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Return holdings (positions + cash) for a specific statement."""
    stmt = conn.execute(
        """SELECT account_id, period_end,
                  opening_balance_cad, opening_balance_usd,
                  closing_balance_cad, closing_balance_usd
           FROM statement_registry WHERE id = ?""",
        (statement_id,),
    ).fetchone()
    if not stmt:
        raise HTTPException(404, "Statement not found")

    # Find accounts.id matching the statement's account_id string
    acct = conn.execute(
        "SELECT id FROM accounts WHERE account_id = ?",
        (stmt["account_id"],),
    ).fetchone()

    positions = []
    if acct and stmt["period_end"]:
        rows = conn.execute(
            """
            SELECT
                i.symbol,
                i.asset_type,
                ps.quantity,
                ps.market_value  AS market_price,
                ps.market_price  AS market_value,
                ps.book_cost,
                ps.market_currency AS currency
            FROM position_state ps
            JOIN instruments i ON i.id = ps.instrument_id
            WHERE ps.account_id = ? AND ps.as_of_date = ?
            ORDER BY i.asset_type, i.symbol
            """,
            (acct["id"], stmt["period_end"]),
        ).fetchall()
        positions = [dict(r) for r in rows]

    return {
        "positions": positions,
        "cash": {
            "opening_cad": stmt["opening_balance_cad"],
            "closing_cad": stmt["closing_balance_cad"],
            "opening_usd": stmt["opening_balance_usd"],
            "closing_usd": stmt["closing_balance_usd"],
        },
    }


@router.get("/{statement_id}/pdf")
def get_statement_pdf(
    statement_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Stream the original PDF file for a statement."""
    row = conn.execute(
        "SELECT source_file FROM statement_registry WHERE id = ?",
        (statement_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Statement not found")

    pdf_path = Path(row["source_file"])
    if not pdf_path.exists():
        raise HTTPException(404, f"PDF file not found on disk: {pdf_path.name}")

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=pdf_path.name,
    )


@router.get("/{statement_id}")
def get_statement(
    statement_id: int,
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Get a single statement's metadata and docling JSON."""
    row = conn.execute(
        """
        SELECT
            sr.id,
            sr.source_file,
            sr.institution,
            sr.account_id,
            sr.period_start,
            sr.period_end,
            sr.status,
            sr.transaction_count,
            sr.balance_validated,
            sr.docling_json
        FROM statement_registry sr
        WHERE sr.id = ?
        """,
        (statement_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Statement not found")

    result = dict(row)
    # Parse docling_json from TEXT to a proper JSON object
    if result["docling_json"]:
        try:
            result["docling_json"] = json.loads(result["docling_json"])
        except (json.JSONDecodeError, TypeError):
            result["docling_json"] = None
    return result

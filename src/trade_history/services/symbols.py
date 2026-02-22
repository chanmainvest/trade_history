from __future__ import annotations

import sqlite3
from typing import Any

from trade_history.db.sqlite import get_connection, init_db
from trade_history.ingest.market import (
    DEFAULT_SYMBOL_ALIAS_MAP,
    load_symbol_overrides,
    refresh_instrument_metadata,
    resolve_market_symbol,
)


def _fetch_catalog_rows(
    conn: sqlite3.Connection,
    query: str | None = None,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    symbol_filter_sql = ""
    if query:
        symbol_filter_sql = "AND UPPER(i.symbol_norm) LIKE ?"
        params.append(f"%{query.strip().upper()}%")

    rows = conn.execute(
        f"""
        SELECT
          i.symbol_norm,
          MIN(i.symbol_raw) AS sample_symbol_raw,
          COUNT(DISTINCT e.event_id) AS event_count,
          COUNT(DISTINCT e.account_id) AS account_count,
          COALESCE(MAX(so.market_symbol), '') AS override_market_symbol,
          COALESCE(MAX(so.sector_override), '') AS override_sector,
          COALESCE(MAX(so.notes), '') AS override_notes,
          COALESCE(MAX(CASE WHEN so.is_active = 1 THEN 1 ELSE 0 END), 0) AS override_active,
          COALESCE(MAX(im.sector), '') AS provider_sector,
          COALESCE(MAX(im.industry), '') AS provider_industry,
          COALESCE(MAX(im.exchange), '') AS provider_exchange,
          COALESCE(MAX(i.sector), '') AS instrument_sector
        FROM instruments i
        LEFT JOIN events e ON e.instrument_id = i.instrument_id
        LEFT JOIN symbol_overrides so
          ON so.symbol_norm = i.symbol_norm
         AND so.is_active = 1
        LEFT JOIN instrument_metadata im
          ON im.symbol_norm = i.symbol_norm
         AND im.provider = 'yahoo_search'
        WHERE i.asset_type = 'equity'
          {symbol_filter_sql}
        GROUP BY i.symbol_norm
        ORDER BY event_count DESC, i.symbol_norm ASC
        """,
        params,
    ).fetchall()
    return rows


def list_symbol_catalog(query: str | None = None) -> dict[str, Any]:
    init_db()
    with get_connection() as conn:
        override_map, _ = load_symbol_overrides(conn)
        rows = _fetch_catalog_rows(conn, query=query)

    items: list[dict[str, Any]] = []
    for row in rows:
        symbol_norm = str(row["symbol_norm"])
        resolved_market_symbol = resolve_market_symbol(
            symbol_norm,
            overrides=override_map,
            provider="yahoo",
        )
        default_market_symbol = DEFAULT_SYMBOL_ALIAS_MAP.get(symbol_norm, symbol_norm)
        items.append(
            {
                "symbol_norm": symbol_norm,
                "sample_symbol_raw": row["sample_symbol_raw"],
                "event_count": int(row["event_count"] or 0),
                "account_count": int(row["account_count"] or 0),
                "default_market_symbol": default_market_symbol,
                "resolved_market_symbol": resolved_market_symbol,
                "override_market_symbol": row["override_market_symbol"] or None,
                "override_sector": row["override_sector"] or None,
                "override_notes": row["override_notes"] or None,
                "override_active": bool(row["override_active"]),
                "provider_sector": row["provider_sector"] or None,
                "provider_industry": row["provider_industry"] or None,
                "provider_exchange": row["provider_exchange"] or None,
                "instrument_sector": row["instrument_sector"] or None,
            }
        )
    return {"items": items}


def list_symbol_overrides() -> dict[str, Any]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT symbol_norm, market_symbol, sector_override, notes, is_active, created_at, updated_at
            FROM symbol_overrides
            ORDER BY symbol_norm
            """
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


def upsert_symbol_override(
    symbol_norm: str,
    market_symbol: str | None = None,
    sector_override: str | None = None,
    notes: str | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    init_db()
    normalized_symbol = symbol_norm.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol_norm is required")
    market_value = (market_symbol or normalized_symbol).strip().upper()
    if not market_value:
        raise ValueError("market_symbol cannot be empty")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO symbol_overrides(
              symbol_norm, market_symbol, sector_override, notes, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(symbol_norm) DO UPDATE SET
              market_symbol = excluded.market_symbol,
              sector_override = excluded.sector_override,
              notes = excluded.notes,
              is_active = excluded.is_active,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                normalized_symbol,
                market_value,
                sector_override.strip() if sector_override else None,
                notes.strip() if notes else None,
                1 if is_active else 0,
            ),
        )
        if sector_override:
            conn.execute(
                "UPDATE instruments SET sector = ? WHERE symbol_norm = ?",
                (sector_override.strip(), normalized_symbol),
            )
        conn.commit()

        row = conn.execute(
            """
            SELECT symbol_norm, market_symbol, sector_override, notes, is_active, created_at, updated_at
            FROM symbol_overrides
            WHERE symbol_norm = ?
            """,
            (normalized_symbol,),
        ).fetchone()
        return dict(row) if row else {}


def delete_symbol_override(symbol_norm: str) -> dict[str, Any]:
    init_db()
    normalized_symbol = symbol_norm.strip().upper()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE symbol_overrides
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE symbol_norm = ?
            """,
            (normalized_symbol,),
        )
        conn.commit()
    return {"symbol_norm": normalized_symbol, "is_active": False}


def refresh_sectors(symbols: list[str] | None = None) -> dict[str, Any]:
    init_db()
    normalized_symbols = [s.strip().upper() for s in (symbols or []) if s.strip()]
    with get_connection() as conn:
        override_map, sector_map = load_symbol_overrides(conn)
        result = refresh_instrument_metadata(
            sqlite_conn=conn,
            symbols=normalized_symbols or None,
            market_symbol_overrides=override_map,
            sector_overrides=sector_map,
        )
    return result

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import sqlite3
from typing import Any, Literal

from trade_history.db.duck import connect as duck_connect
from trade_history.db.sqlite import get_connection


SortOrder = Literal["asc", "desc"]
GroupBy = Literal["total", "account", "institution"]


def _latest_usdcad_rate(conn: sqlite3.Connection, as_of: date | None = None) -> float:
    if as_of:
        row = conn.execute(
            "SELECT rate FROM fx_rates WHERE pair = 'USD/CAD' AND date <= ? ORDER BY date DESC LIMIT 1",
            (as_of.isoformat(),),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT rate FROM fx_rates WHERE pair = 'USD/CAD' ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return 1.35
    return float(row["rate"])


def _convert_currency(amount: float | None, source_currency: str | None, target_currency: str, usdcad: float) -> float | None:
    if amount is None:
        return None
    src = (source_currency or target_currency).upper()
    tgt = target_currency.upper()
    if src == tgt:
        return amount
    if src == "USD" and tgt == "CAD":
        return amount * usdcad
    if src == "CAD" and tgt == "USD":
        return amount / usdcad if usdcad else None
    return amount


def _latest_prices() -> dict[str, tuple[float, str]]:
    conn = duck_connect()
    try:
        rows = conn.execute(
            """
            SELECT symbol_norm, close, currency
            FROM (
              SELECT
                symbol_norm,
                close,
                currency,
                ROW_NUMBER() OVER (PARTITION BY symbol_norm ORDER BY trade_date DESC) AS rn
              FROM canonical_prices
            ) x
            WHERE rn = 1
            """
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return {str(symbol): (float(close), str(currency or "")) for symbol, close, currency in rows if close is not None}


def list_trades(
    page: int = 1,
    page_size: int = 200,
    sort_by: str = "trade_date",
    sort_order: SortOrder = "desc",
    account_id: str | None = None,
    institution: str | None = None,
    symbol: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    allowed_sort = {
        "trade_date": "e.trade_date",
        "account_id": "e.account_id",
        "institution": "a.institution",
        "symbol": "i.symbol_norm",
        "quantity": "e.quantity",
        "price": "e.price",
        "gross_amount": "e.gross_amount",
        "realized_pl": "lc.realized_pl_native",
    }
    sort_column = allowed_sort.get(sort_by, "e.trade_date")
    sort_direction = "ASC" if sort_order.lower() == "asc" else "DESC"
    offset = max(page - 1, 0) * page_size

    filters = []
    params: list[Any] = []
    if account_id:
        filters.append("e.account_id = ?")
        params.append(account_id)
    if institution:
        filters.append("a.institution = ?")
        params.append(institution)
    if symbol:
        filters.append("i.symbol_norm = ?")
        params.append(symbol.upper())
    if event_type:
        filters.append("e.event_type = ?")
        params.append(event_type.lower())
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
      SELECT
        e.event_id,
        e.trade_date,
        e.settle_date,
        e.account_id,
        a.institution,
        e.event_type,
        e.side,
        e.quantity,
        e.price,
        e.gross_amount,
        e.commission,
        e.fees,
        e.currency,
        i.symbol_norm AS symbol,
        i.asset_type,
        lc.realized_pl_native
      FROM events e
      JOIN accounts a ON a.account_id = e.account_id
      LEFT JOIN instruments i ON i.instrument_id = e.instrument_id
      LEFT JOIN lot_closures lc ON lc.close_event_id = e.event_id
      {where_clause}
      ORDER BY {sort_column} {sort_direction}, e.event_id {sort_direction}
      LIMIT ? OFFSET ?
    """

    count_query = f"""
      SELECT COUNT(*) AS c
      FROM events e
      JOIN accounts a ON a.account_id = e.account_id
      LEFT JOIN instruments i ON i.instrument_id = e.instrument_id
      {where_clause}
    """

    with get_connection() as conn:
        total = int(conn.execute(count_query, params).fetchone()["c"])
        rows = [dict(r) for r in conn.execute(query, [*params, page_size, offset]).fetchall()]
    return {"total": total, "items": rows}


def list_closed_positions(
    page: int = 1,
    page_size: int = 200,
    account_id: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    filters = []
    params: list[Any] = []
    if account_id:
        filters.append("lc.account_id = ?")
        params.append(account_id)
    if symbol:
        filters.append("i.symbol_norm = ?")
        params.append(symbol.upper())
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
      SELECT
        lc.id,
        lc.close_event_id,
        e.trade_date AS close_date,
        lc.account_id,
        a.institution,
        i.symbol_norm AS symbol,
        lc.quantity_closed,
        lc.proceeds_native,
        lc.cost_native,
        lc.realized_pl_native,
        lc.currency
      FROM lot_closures lc
      JOIN events e ON e.event_id = lc.close_event_id
      JOIN accounts a ON a.account_id = lc.account_id
      JOIN instruments i ON i.instrument_id = lc.instrument_id
      {where_clause}
      ORDER BY date(e.trade_date) DESC, lc.id DESC
      LIMIT ? OFFSET ?
    """
    count_query = f"""
      SELECT COUNT(*) AS c
      FROM lot_closures lc
      JOIN instruments i ON i.instrument_id = lc.instrument_id
      {where_clause}
    """

    with get_connection() as conn:
        total = int(conn.execute(count_query, params).fetchone()["c"])
        rows = [dict(r) for r in conn.execute(query, [*params, page_size, offset]).fetchall()]
    return {"total": total, "items": rows}


@dataclass(slots=True)
class AssetRow:
    group_key: str
    account_id: str
    institution: str
    symbol: str
    asset_type: str
    sector: str
    quantity: float
    currency_native: str
    price_native: float | None
    market_value_native: float | None
    market_value_display: float | None
    cost_native: float
    unrealized_pl_native: float | None


def asset_values(
    display_currency: str = "CAD",
    group_by: GroupBy = "total",
    institution: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    prices = _latest_prices()
    target_currency = display_currency.upper()

    with get_connection() as conn:
        usdcad = _latest_usdcad_rate(conn)
        rows = conn.execute(
            """
            SELECT
              p.account_id,
              a.institution,
              i.symbol_norm AS symbol,
              COALESCE(NULLIF(TRIM(i.asset_type), ''), 'equity') AS asset_type,
              COALESCE(
                NULLIF(TRIM(so.sector_override), ''),
                NULLIF(TRIM(i.sector), ''),
                NULLIF(TRIM(im.sector), ''),
                'Unknown'
              ) AS sector,
              p.quantity,
              p.currency,
              p.cost_total_native
            FROM position_state p
            JOIN accounts a ON a.account_id = p.account_id
            JOIN instruments i ON i.instrument_id = p.instrument_id
            LEFT JOIN symbol_overrides so
              ON so.symbol_norm = i.symbol_norm
             AND so.is_active = 1
            LEFT JOIN instrument_metadata im
              ON im.symbol_norm = i.symbol_norm
             AND im.provider = 'yahoo_search'
            WHERE (? IS NULL OR a.institution = ?)
              AND (? IS NULL OR p.account_id = ?)
            ORDER BY a.institution, p.account_id, i.symbol_norm
            """,
            (institution, institution, account_id, account_id),
        ).fetchall()

    expanded: list[AssetRow] = []
    for row in rows:
        symbol = str(row["symbol"])
        native_ccy = str(row["currency"] or "CAD").upper()
        quantity = float(row["quantity"] or 0.0)
        price_info = prices.get(symbol)
        if price_info:
            px = price_info[0]
            px_ccy = price_info[1].upper() if price_info[1] else native_ccy
        else:
            px = None
            px_ccy = native_ccy
        mv_native = quantity * px if px is not None else None
        mv_display = _convert_currency(mv_native, px_ccy, target_currency, usdcad) if mv_native is not None else None
        cost_native = float(row["cost_total_native"] or 0.0)
        unrealized = (mv_native - cost_native) if mv_native is not None else None

        if group_by == "account":
            group_key = f"{row['institution']} | {row['account_id']}"
        elif group_by == "institution":
            group_key = str(row["institution"])
        else:
            group_key = "total"

        expanded.append(
            AssetRow(
                group_key=group_key,
                account_id=str(row["account_id"]),
                institution=str(row["institution"]),
                symbol=symbol,
                asset_type=str(row["asset_type"] or "equity").lower(),
                sector=str(row["sector"]),
                quantity=quantity,
                currency_native=native_ccy,
                price_native=px,
                market_value_native=mv_native,
                market_value_display=mv_display,
                cost_native=cost_native,
                unrealized_pl_native=unrealized,
            )
        )

    grouped: dict[str, dict[str, Any]] = {}
    for row in expanded:
        g = grouped.setdefault(
            row.group_key,
            {
                "group_key": row.group_key,
                "display_currency": target_currency,
                "total_market_value_display": 0.0,
                "total_market_value_native": 0.0,
                "positions": [],
            },
        )
        g["positions"].append(asdict(row))
        if row.market_value_display is not None:
            g["total_market_value_display"] += row.market_value_display
        if row.market_value_native is not None:
            g["total_market_value_native"] += row.market_value_native

    return {"display_currency": target_currency, "items": list(grouped.values())}


def sector_allocation(display_currency: str = "CAD") -> dict[str, Any]:
    assets = asset_values(display_currency=display_currency, group_by="total")
    target_currency = display_currency.upper()
    sector_totals: dict[str, float] = {}
    for group in assets["items"]:
        for position in group["positions"]:
            sector = str(position.get("sector") or "Unknown")
            mv = position.get("market_value_display")
            if mv is None:
                continue
            sector_totals[sector] = sector_totals.get(sector, 0.0) + float(mv)
    grand_total = sum(sector_totals.values())
    items = []
    for sector, value in sorted(sector_totals.items(), key=lambda x: x[1], reverse=True):
        pct = (value / grand_total * 100.0) if grand_total else 0.0
        items.append({"sector": sector, "value": value, "percentage": pct, "currency": target_currency})
    return {"display_currency": target_currency, "total": grand_total, "items": items}


def monthly_statement_reconciliation(
    display_currency: str = "CAD",
    institution: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    target_currency = display_currency.upper()
    with get_connection() as conn:
        usdcad = _latest_usdcad_rate(conn)
        snapshot_rows = conn.execute(
            """
            SELECT
              a.institution,
              ss.account_id,
              COALESCE(ss.snapshot_date, sf.period_end, sf.period_start) AS snapshot_date,
              ss.metric_code,
              COALESCE(ss.currency, 'CAD') AS currency,
              ss.value_native
            FROM statement_snapshots ss
            JOIN statement_files sf ON sf.id = ss.source_file_id
            JOIN accounts a ON a.account_id = ss.account_id
            WHERE (? IS NULL OR a.institution = ?)
              AND (? IS NULL OR ss.account_id = ?)
            ORDER BY date(COALESCE(ss.snapshot_date, sf.period_end, sf.period_start)), ss.id
            """,
            (institution, institution, account_id, account_id),
        ).fetchall()
        txn_rows = conn.execute(
            """
            SELECT
              a.institution,
              e.account_id,
              substr(e.trade_date, 1, 7) AS month_key,
              COALESCE(e.currency, 'CAD') AS currency,
              COUNT(*) AS txn_event_count,
              SUM(COALESCE(e.gross_amount, 0.0)) AS txn_net_cash_flow_native,
              SUM(COALESCE(e.commission, 0.0) + COALESCE(e.fees, 0.0)) AS txn_fee_total_native
            FROM events e
            JOIN accounts a ON a.account_id = e.account_id
            WHERE (? IS NULL OR a.institution = ?)
              AND (? IS NULL OR e.account_id = ?)
            GROUP BY a.institution, e.account_id, month_key, COALESCE(e.currency, 'CAD')
            """,
            (institution, institution, account_id, account_id),
        ).fetchall()

    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def ensure_group(inst: str, acct: str, month: str, ccy: str) -> dict[str, Any]:
        key = (inst, acct, month, ccy)
        return grouped.setdefault(
            key,
            {
                "month": month,
                "institution": inst,
                "account_id": acct,
                "currency_native": ccy,
                "statement_cash_opening_native": None,
                "statement_cash_closing_native": None,
                "statement_portfolio_native": None,
                "statement_previous_value_native": None,
                "txn_event_count": 0,
                "txn_net_cash_flow_native": 0.0,
                "txn_fee_total_native": 0.0,
                "derived_cash_closing_native": None,
                "reconciliation_gap_native": None,
                "_has_total_cash_closing": False,
            },
        )

    for row in snapshot_rows:
        snapshot_date = row["snapshot_date"]
        if not snapshot_date:
            continue
        month = str(snapshot_date)[:7]
        institution_name = str(row["institution"])
        account = str(row["account_id"])
        currency = str(row["currency"] or "CAD").upper()
        metric_code = str(row["metric_code"])
        value = float(row["value_native"] or 0.0)
        entry = ensure_group(institution_name, account, month, currency)

        if metric_code == "cash_opening":
            entry["statement_cash_opening_native"] = value
        elif metric_code in {"cash_closing", "cash_closing_total"}:
            if metric_code == "cash_closing_total":
                entry["_has_total_cash_closing"] = True
            if entry["statement_cash_closing_native"] is None or metric_code == "cash_closing_total":
                entry["statement_cash_closing_native"] = value
        elif metric_code in {"portfolio_total", "account_value_current"}:
            if entry["statement_portfolio_native"] is None or metric_code == "portfolio_total":
                entry["statement_portfolio_native"] = value
        elif metric_code == "account_value_previous":
            entry["statement_previous_value_native"] = value

    for row in txn_rows:
        month = str(row["month_key"] or "")
        if not month:
            continue
        institution_name = str(row["institution"])
        account = str(row["account_id"])
        currency = str(row["currency"] or "CAD").upper()
        entry = ensure_group(institution_name, account, month, currency)
        entry["txn_event_count"] = int(row["txn_event_count"] or 0)
        entry["txn_net_cash_flow_native"] = float(row["txn_net_cash_flow_native"] or 0.0)
        entry["txn_fee_total_native"] = float(row["txn_fee_total_native"] or 0.0)

    items: list[dict[str, Any]] = []
    for _, item in grouped.items():
        opening = item["statement_cash_opening_native"]
        net_cash = item["txn_net_cash_flow_native"]
        closing = item["statement_cash_closing_native"]
        if opening is not None:
            item["derived_cash_closing_native"] = float(opening) + float(net_cash)
        if item["derived_cash_closing_native"] is not None and closing is not None:
            item["reconciliation_gap_native"] = float(closing) - float(item["derived_cash_closing_native"])

        for field in (
            "statement_cash_opening_native",
            "statement_cash_closing_native",
            "statement_portfolio_native",
            "statement_previous_value_native",
            "txn_net_cash_flow_native",
            "txn_fee_total_native",
            "derived_cash_closing_native",
            "reconciliation_gap_native",
        ):
            display_key = field.replace("_native", "_display")
            item[display_key] = _convert_currency(item[field], item["currency_native"], target_currency, usdcad)

        gap_display = item["reconciliation_gap_display"]
        if gap_display is None:
            item["status"] = "missing_snapshot"
        elif abs(float(gap_display)) <= 1.0:
            item["status"] = "ok"
        else:
            item["status"] = "warning"
        item.pop("_has_total_cash_closing", None)
        items.append(item)

    items.sort(
        key=lambda x: (
            x["month"],
            x["institution"],
            x["account_id"],
            x["currency_native"],
        ),
        reverse=True,
    )
    return {"display_currency": target_currency, "items": items}


def monthly_reconciliation_snapshot_lines(
    month: str,
    account_id: str,
    currency_native: str | None = None,
    display_currency: str = "CAD",
    institution: str | None = None,
) -> dict[str, Any]:
    target_currency = display_currency.upper()
    with get_connection() as conn:
        usdcad = _latest_usdcad_rate(conn)
        rows = conn.execute(
            """
            SELECT
              ss.id,
              a.institution,
              ss.account_id,
              COALESCE(ss.snapshot_date, sf.period_end, sf.period_start) AS snapshot_date,
              ss.metric_code,
              COALESCE(ss.currency, 'CAD') AS currency_native,
              ss.value_native,
              sf.file_path,
              ss.source_line_ref,
              ss.raw_line
            FROM statement_snapshots ss
            JOIN statement_files sf ON sf.id = ss.source_file_id
            JOIN accounts a ON a.account_id = ss.account_id
            WHERE ss.account_id = ?
              AND substr(COALESCE(ss.snapshot_date, sf.period_end, sf.period_start), 1, 7) = ?
              AND (? IS NULL OR UPPER(COALESCE(ss.currency, 'CAD')) = UPPER(?))
              AND (? IS NULL OR a.institution = ?)
            ORDER BY
              date(COALESCE(ss.snapshot_date, sf.period_end, sf.period_start)),
              ss.metric_code,
              ss.id
            """,
            (account_id, month, currency_native, currency_native, institution, institution),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        ccy = str(row["currency_native"] or "CAD").upper()
        value_native = float(row["value_native"] or 0.0)
        value_display = _convert_currency(value_native, ccy, target_currency, usdcad)
        file_path = str(row["file_path"] or "")
        file_name = file_path.replace("\\", "/").split("/")[-1] if file_path else ""
        items.append(
            {
                "id": int(row["id"]),
                "institution": str(row["institution"]),
                "account_id": str(row["account_id"]),
                "month": month,
                "snapshot_date": row["snapshot_date"],
                "metric_code": str(row["metric_code"]),
                "currency_native": ccy,
                "value_native": value_native,
                "value_display": value_display,
                "file_path": file_path,
                "file_name": file_name,
                "source_line_ref": row["source_line_ref"],
                "raw_line": row["raw_line"],
            }
        )

    return {"display_currency": target_currency, "items": items}

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import re
import time
import sqlite3
from typing import Any
from urllib.parse import quote_plus

import requests

from trade_history.db.duck import connect as duck_connect, init_db as init_duckdb
from trade_history.db.sqlite import get_connection, init_db as init_sqlite


def _stooq_symbol(market_symbol: str) -> str:
    normalized = market_symbol.lower()
    if normalized.endswith(".to") or normalized.endswith(".v"):
        return normalized
    if "." in normalized:
        return normalized
    return f"{normalized}.us"


def _yahoo_symbol(market_symbol: str) -> str:
    normalized = market_symbol.upper()
    if normalized.endswith(".TO") or normalized.endswith(".V"):
        return normalized
    return normalized


def _infer_currency(market_symbol: str) -> str:
    upper = market_symbol.upper()
    if upper.endswith(".TO") or upper.endswith(".V"):
        return "CAD"
    return "USD"


@dataclass(slots=True)
class PriceIngestReport:
    symbols_requested: int = 0
    stooq_rows: int = 0
    yahoo_rows: int = 0
    canonical_rows: int = 0
    sector_metadata_rows: int = 0
    sectors_updated: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols_requested": self.symbols_requested,
            "stooq_rows": self.stooq_rows,
            "yahoo_rows": self.yahoo_rows,
            "canonical_rows": self.canonical_rows,
            "sector_metadata_rows": self.sector_metadata_rows,
            "sectors_updated": self.sectors_updated,
            "errors": self.errors or [],
        }


DEFAULT_SYMBOL_ALIAS_MAP: dict[str, str] = {
    "ADOBE": "ADBE",
    "ADVANCED": "AMD",
    "AIRBNB": "ABNB",
    "ALPHABET": "GOOGL",
    "APPLE": "AAPL",
    "B2GOLD": "BTO.TO",
    "BARRICK": "ABX.TO",
    "BCEINC": "BCE.TO",
    "CELESTICA": "CLS.TO",
    "CENOVUS": "CVE.TO",
    "ENBRIDGE": "ENB.TO",
    "FORTIS": "FTS.TO",
    "HECLA": "HL",
    "MICROCHIP": "MCHP",
    "MICROSOFT": "MSFT",
    "NATIONAL": "NA.TO",
    "NEWMONT": "NEM",
    "NEXTERA": "NEE",
    "NUTRIEN": "NTR.TO",
    "NVIDIA": "NVDA",
    "PFIZER": "PFE",
    "QUALCOMM": "QCOM",
    "REDDIT": "RDDT",
    "ROGERS": "RCI.B.TO",
    "ROYALBANK": "RY.TO",
    "SANDSTORM": "SSL.TO",
    "SHOPIFYINC": "SHOP.TO",
    "SUNCOR": "SU.TO",
    "TELUSCORP": "T.TO",
    "TORONTO": "TD.TO",
    "TSLA": "TSLA",
    "WHEATON": "WPM.TO",
}

_TICKER_LIKE = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z]{1,4})?$")
_BLOCKED_SYMBOLS = {
    "BANK",
    "CDN",
    "CHECK",
    "CO",
    "DEBIT",
    "FUNDS",
    "GREEN",
    "HYDRO",
    "INCOME",
    "INT",
    "INTL",
    "OR",
    "PAN",
    "PAPER",
    "SERIES",
    "SOUTH",
    "SUPER",
    "TO",
    "TRANSFER",
    "WAVE",
}


def resolve_market_symbol(
    symbol_norm: str,
    overrides: dict[str, str] | None = None,
    provider: str = "yahoo",
) -> str:
    canonical = (overrides or {}).get(symbol_norm.upper()) or DEFAULT_SYMBOL_ALIAS_MAP.get(symbol_norm.upper(), symbol_norm.upper())
    if provider == "stooq":
        return canonical
    return canonical


def _is_symbol_eligible(symbol_norm: str, overrides: dict[str, str] | None = None) -> bool:
    candidate = resolve_market_symbol(symbol_norm, overrides=overrides, provider="yahoo")
    if candidate in _BLOCKED_SYMBOLS:
        return False
    return bool(_TICKER_LIKE.match(candidate))


def load_symbol_overrides(sqlite_conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    market_map: dict[str, str] = {}
    sector_map: dict[str, str] = {}
    rows = sqlite_conn.execute(
        """
        SELECT symbol_norm, market_symbol, sector_override
        FROM symbol_overrides
        WHERE is_active = 1
        """
    ).fetchall()
    for row in rows:
        symbol_norm = str(row["symbol_norm"]).upper()
        market_symbol = str(row["market_symbol"]).upper().strip()
        if market_symbol:
            market_map[symbol_norm] = market_symbol
        sector_override = row["sector_override"]
        if sector_override:
            sector_map[symbol_norm] = str(sector_override).strip()
    return market_map, sector_map


def _load_symbols(
    sqlite_conn: sqlite3.Connection,
    overrides: dict[str, str] | None = None,
) -> list[str]:
    rows = sqlite_conn.execute(
        """
        SELECT DISTINCT symbol_norm
        FROM instruments
        WHERE asset_type = 'equity'
          AND symbol_norm NOT IN ('CASH', 'UNKNOWN')
        ORDER BY symbol_norm
        """
    ).fetchall()
    symbols = [str(r["symbol_norm"]) for r in rows]
    return [symbol for symbol in symbols if _is_symbol_eligible(symbol, overrides=overrides)]


def ingest_prices(
    lookback_years: int = 15,
    use_stooq: bool = True,
    use_yahoo: bool = True,
    refresh_sector_metadata: bool = True,
) -> PriceIngestReport:
    init_duckdb()
    init_sqlite()
    report = PriceIngestReport(errors=[])
    end = date.today()
    start = end - timedelta(days=365 * lookback_years)

    sqlite_conn = get_connection()
    duck_conn = duck_connect()
    yahoo_session = requests.Session() if use_yahoo else None
    try:
        symbol_override_map, sector_override_map = load_symbol_overrides(sqlite_conn)
        symbols = _load_symbols(sqlite_conn, overrides=symbol_override_map)
        report.symbols_requested = len(symbols)
        for symbol in symbols:
            market_symbol = resolve_market_symbol(symbol, symbol_override_map, provider="yahoo")
            if use_stooq:
                try:
                    inserted = _ingest_stooq_symbol(duck_conn, symbol, market_symbol, start, end)
                    report.stooq_rows += inserted
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"stooq:{symbol}:{exc}")
            if use_yahoo:
                try:
                    inserted = _ingest_yahoo_symbol(
                        duck_conn,
                        symbol,
                        market_symbol,
                        start,
                        end,
                        session=yahoo_session,
                    )
                    report.yahoo_rows += inserted
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(f"yahoo:{symbol}:{exc}")
                time.sleep(0.05)

        if refresh_sector_metadata:
            metadata_stats = refresh_instrument_metadata(
                sqlite_conn=sqlite_conn,
                symbols=symbols,
                market_symbol_overrides=symbol_override_map,
                sector_overrides=sector_override_map,
                session=yahoo_session,
            )
            report.sector_metadata_rows = metadata_stats["metadata_rows"]
            report.sectors_updated = metadata_stats["sectors_updated"]

        report.canonical_rows = rebuild_canonical_prices(duck_conn)
    finally:
        if yahoo_session is not None:
            yahoo_session.close()
        sqlite_conn.close()
        duck_conn.close()
    return report


def _ingest_stooq_symbol(
    duck_conn: Any,
    symbol: str,
    market_symbol: str,
    start: date,
    end: date,
) -> int:
    url = f"https://stooq.com/q/d/l/?s={_stooq_symbol(market_symbol)}&i=d"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    lines = [line for line in response.text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return 0
    duck_conn.execute("DELETE FROM raw_stooq_prices WHERE symbol_norm = ?", [symbol])
    inserted = 0
    for row in lines[1:]:
        parts = row.split(",")
        if len(parts) < 6:
            continue
        trade_date = date.fromisoformat(parts[0])
        if trade_date < start or trade_date > end:
            continue
        payload = json.dumps(
            {
                "date": parts[0],
                "open": parts[1],
                "high": parts[2],
                "low": parts[3],
                "close": parts[4],
                "volume": parts[5],
            }
        )
        duck_conn.execute(
            """
            INSERT INTO raw_stooq_prices(
              symbol_norm, trade_date, open, high, low, close, volume, currency, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                symbol,
                parts[0],
                float(parts[1]) if parts[1] not in ("", "0") else None,
                float(parts[2]) if parts[2] not in ("", "0") else None,
                float(parts[3]) if parts[3] not in ("", "0") else None,
                float(parts[4]) if parts[4] not in ("", "0") else None,
                float(parts[5]) if parts[5] not in ("", "0") else None,
                _infer_currency(market_symbol),
                payload,
            ],
        )
        inserted += 1
    return inserted


def _ingest_yahoo_symbol(
    duck_conn: Any,
    symbol: str,
    market_symbol: str,
    start: date,
    end: date,
    session: requests.Session | None = None,
) -> int:
    yahoo_symbol = _yahoo_symbol(market_symbol)
    period1 = int(time.mktime(start.timetuple()))
    period2 = int(time.mktime((end + timedelta(days=1)).timetuple()))
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
    )
    request_session = session or requests.Session()
    response = None
    for attempt in range(3):
        response = request_session.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        if response.status_code == 429:
            time.sleep(1 + attempt)
            continue
        response.raise_for_status()
        break
    if response is None:
        return 0
    if response.status_code == 429:
        response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result", [])
    if not result:
        return 0
    series = result[0]
    timestamps = series.get("timestamp") or []
    quote = ((series.get("indicators") or {}).get("quote") or [{}])[0]
    currency = (series.get("meta") or {}).get("currency", _infer_currency(market_symbol))

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    duck_conn.execute("DELETE FROM raw_yahoo_prices WHERE symbol_norm = ?", [symbol])
    inserted = 0
    for idx, ts in enumerate(timestamps):
        dt = date.fromtimestamp(ts)
        open_px = opens[idx] if idx < len(opens) else None
        high_px = highs[idx] if idx < len(highs) else None
        low_px = lows[idx] if idx < len(lows) else None
        close_px = closes[idx] if idx < len(closes) else None
        vol = volumes[idx] if idx < len(volumes) else None
        if close_px is None:
            continue
        payload = json.dumps(
            {
                "open": open_px,
                "high": high_px,
                "low": low_px,
                "close": close_px,
                "volume": vol,
            }
        )
        duck_conn.execute(
            """
            INSERT INTO raw_yahoo_prices(
              symbol_norm, trade_date, open, high, low, close, volume, currency, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                symbol,
                dt.isoformat(),
                float(open_px) if open_px is not None else None,
                float(high_px) if high_px is not None else None,
                float(low_px) if low_px is not None else None,
                float(close_px) if close_px is not None else None,
                float(vol) if vol is not None else None,
                str(currency).upper() if currency else _infer_currency(market_symbol),
                payload,
            ],
        )
        inserted += 1
    return inserted


def _fetch_yahoo_search_metadata(
    session: requests.Session,
    market_symbol: str,
) -> dict[str, Any] | None:
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search"
        f"?q={quote_plus(market_symbol)}&quotes_count=8&news_count=0"
    )
    response = None
    for attempt in range(3):
        response = session.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        if response.status_code == 429:
            time.sleep(1 + attempt)
            continue
        response.raise_for_status()
        break
    if response is None:
        return None
    if response.status_code == 429:
        return None

    payload = response.json()
    quotes = payload.get("quotes") or []
    if not quotes:
        return None

    target_upper = market_symbol.upper()
    best: dict[str, Any] | None = None
    best_score = -1
    for quote_item in quotes:
        quote_symbol = str(quote_item.get("symbol") or "").upper()
        score = 0
        if quote_symbol == target_upper:
            score += 100
        if quote_symbol.replace("-", ".") == target_upper or quote_symbol.replace(".", "-") == target_upper:
            score += 80
        if target_upper in quote_symbol or quote_symbol in target_upper:
            score += 40
        quote_type = str(quote_item.get("quoteType") or "")
        if quote_type == "EQUITY":
            score += 10
        if score > best_score:
            best_score = score
            best = quote_item
    return best


def refresh_instrument_metadata(
    sqlite_conn: sqlite3.Connection,
    symbols: list[str] | None = None,
    market_symbol_overrides: dict[str, str] | None = None,
    sector_overrides: dict[str, str] | None = None,
    session: requests.Session | None = None,
) -> dict[str, int]:
    if symbols is None:
        symbols = _load_symbols(sqlite_conn, overrides=market_symbol_overrides)
    override_map = market_symbol_overrides or {}
    sector_map = sector_overrides or {}
    request_session = session or requests.Session()
    metadata_rows = 0
    sectors_updated = 0

    for symbol_norm in symbols:
        market_symbol = resolve_market_symbol(symbol_norm, override_map, provider="yahoo")
        metadata = _fetch_yahoo_search_metadata(request_session, market_symbol)
        if metadata:
            sqlite_conn.execute(
                """
                INSERT INTO instrument_metadata(
                  symbol_norm, provider, market_symbol, display_name, quote_type,
                  sector, industry, exchange, source_json, updated_at
                ) VALUES (?, 'yahoo_search', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(symbol_norm, provider) DO UPDATE SET
                  market_symbol = excluded.market_symbol,
                  display_name = excluded.display_name,
                  quote_type = excluded.quote_type,
                  sector = excluded.sector,
                  industry = excluded.industry,
                  exchange = excluded.exchange,
                  source_json = excluded.source_json,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    symbol_norm,
                    market_symbol,
                    metadata.get("longname") or metadata.get("shortname"),
                    metadata.get("quoteType"),
                    metadata.get("sectorDisp") or metadata.get("sector"),
                    metadata.get("industryDisp") or metadata.get("industry"),
                    metadata.get("exchDisp") or metadata.get("exchange"),
                    json.dumps(metadata),
                ),
            )
            metadata_rows += 1
            discovered_sector = metadata.get("sectorDisp") or metadata.get("sector")
        else:
            discovered_sector = None

        final_sector = sector_map.get(symbol_norm.upper()) or discovered_sector
        if final_sector:
            sqlite_conn.execute(
                """
                UPDATE instruments
                SET sector = ?
                WHERE symbol_norm = ?
                """,
                (str(final_sector), symbol_norm),
            )
            sectors_updated += 1
        time.sleep(0.03)
    sqlite_conn.commit()
    return {"metadata_rows": metadata_rows, "sectors_updated": sectors_updated}


def rebuild_canonical_prices(duck_conn: Any) -> int:
    duck_conn.execute("DELETE FROM price_crossref")
    duck_conn.execute("DELETE FROM canonical_prices")

    duck_conn.execute(
        """
        INSERT INTO price_crossref (
          symbol_norm, trade_date, stooq_close, yahoo_close, quality_flag, canonical_source, canonical_close
        )
        SELECT
          COALESCE(s.symbol_norm, y.symbol_norm) AS symbol_norm,
          COALESCE(s.trade_date, y.trade_date) AS trade_date,
          s.close AS stooq_close,
          y.close AS yahoo_close,
          CASE
            WHEN s.close IS NOT NULL AND y.close IS NOT NULL AND ABS(s.close - y.close) <= 0.01 THEN 'both_match'
            WHEN s.close IS NOT NULL AND y.close IS NOT NULL THEN 'both_diff'
            WHEN s.close IS NOT NULL THEN 'stooq_only'
            ELSE 'yahoo_only'
          END AS quality_flag,
          CASE
            WHEN s.close IS NOT NULL THEN 'stooq'
            ELSE 'yahoo'
          END AS canonical_source,
          COALESCE(s.close, y.close) AS canonical_close
        FROM raw_stooq_prices s
        FULL OUTER JOIN raw_yahoo_prices y
          ON s.symbol_norm = y.symbol_norm
         AND s.trade_date = y.trade_date
        """
    )

    duck_conn.execute(
        """
        INSERT INTO canonical_prices (
          symbol_norm, trade_date, open, high, low, close, volume, currency, source
        )
        SELECT
          x.symbol_norm,
          x.trade_date,
          CASE WHEN x.canonical_source = 'stooq' THEN s.open ELSE y.open END AS open,
          CASE WHEN x.canonical_source = 'stooq' THEN s.high ELSE y.high END AS high,
          CASE WHEN x.canonical_source = 'stooq' THEN s.low ELSE y.low END AS low,
          x.canonical_close AS close,
          CASE WHEN x.canonical_source = 'stooq' THEN s.volume ELSE y.volume END AS volume,
          CASE WHEN x.canonical_source = 'stooq' THEN s.currency ELSE y.currency END AS currency,
          x.canonical_source AS source
        FROM price_crossref x
        LEFT JOIN raw_stooq_prices s
          ON s.symbol_norm = x.symbol_norm
         AND s.trade_date = x.trade_date
        LEFT JOIN raw_yahoo_prices y
          ON y.symbol_norm = x.symbol_norm
         AND y.trade_date = x.trade_date
        """
    )

    row = duck_conn.execute("SELECT COUNT(*) AS c FROM canonical_prices").fetchone()
    return int(row[0]) if row else 0

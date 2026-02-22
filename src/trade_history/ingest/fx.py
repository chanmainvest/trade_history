from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
from typing import Any

import requests

from trade_history.db.duck import connect as duck_connect, init_db as init_duckdb
from trade_history.db.sqlite import db_session, init_db as init_sqlite


@dataclass(slots=True)
class FxIngestReport:
    rows_ingested: int
    source: str = "Bank of Canada"

    def to_dict(self) -> dict[str, Any]:
        return {"rows_ingested": self.rows_ingested, "source": self.source}


def ingest_boc_fx(lookback_years: int = 20) -> FxIngestReport:
    init_duckdb()
    init_sqlite()
    end = date.today()
    start = end - timedelta(days=365 * lookback_years)
    url = (
        "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
        f"?start_date={start.isoformat()}&end_date={end.isoformat()}"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    observations = payload.get("observations", [])

    duck_conn = duck_connect()
    try:
        duck_conn.execute("DELETE FROM raw_boc_fx")
        duck_conn.execute("DELETE FROM canonical_fx")
        inserted = 0
        for obs in observations:
            observed_date = obs.get("d")
            rate_data = obs.get("FXUSDCAD", {})
            rate_val = rate_data.get("v")
            if observed_date is None or rate_val is None:
                continue
            rate = float(rate_val)
            duck_conn.execute(
                """
                INSERT INTO raw_boc_fx(observed_date, series_id, value, raw_payload)
                VALUES (?, 'FXUSDCAD', ?, ?)
                """,
                [observed_date, rate, json.dumps(obs)],
            )
            duck_conn.execute(
                """
                INSERT INTO canonical_fx(observed_date, base_currency, quote_currency, rate, source)
                VALUES (?, 'USD', 'CAD', ?, 'BoC')
                """,
                [observed_date, rate],
            )
            inserted += 1
    finally:
        duck_conn.close()

    # Keep a copy in SQLite for easier joins in API queries.
    mirror_conn = duck_connect()
    try:
        rows = mirror_conn.execute(
            "SELECT observed_date, rate FROM canonical_fx ORDER BY observed_date"
        ).fetchall()
    finally:
        mirror_conn.close()

    with db_session() as conn:
        conn.execute("DELETE FROM fx_rates")
        for observed_date, rate in rows:
            conn.execute(
                "INSERT INTO fx_rates(date, pair, rate, source) VALUES(?, 'USD/CAD', ?, 'BoC')",
                (str(observed_date), float(rate)),
            )

    return FxIngestReport(rows_ingested=inserted)

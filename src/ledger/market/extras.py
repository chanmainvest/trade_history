"""Extended yfinance scrapers: dividends, splits, financials, FX.

All writes are idempotent (DELETE+INSERT per symbol). Each call appends a
JSONL audit row to logs/market_scrape.jsonl.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import duckdb
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import DUCKDB_PATH
from ..db import duckdb_store
from ..logging_setup import get_logger, jsonl_path
from .scrape import _held_symbols, _yf_symbol

log = get_logger("market_scrape")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _ticker(yfsym: str):
    import yfinance as yf
    return yf.Ticker(yfsym)


def _audit(jsonl, **row) -> None:
    jsonl.write(json.dumps(row) + "\n")


# --------------------------------------------------------------- dividends
def refresh_dividends(*, sleep_s: float = 1.5) -> None:
    duckdb_store.init_db()
    con = duckdb.connect(str(DUCKDB_PATH))
    jsonl = jsonl_path("market_scrape").open("a", encoding="utf-8")
    try:
        for sym, ccy in _held_symbols():
            yfsym = _yf_symbol(sym, ccy)
            log.info("Dividends %s", yfsym)
            try:
                t = _ticker(yfsym)
                ser = t.dividends
            except Exception as e:
                _audit(jsonl, kind="dividends", symbol=sym, status="fail", err=str(e))
                continue
            if ser is None or ser.empty:
                _audit(jsonl, kind="dividends", symbol=sym, status="empty")
                time.sleep(sleep_s); continue
            df = ser.reset_index()
            df.columns = ["ex_date", "amount"]
            df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
            df["symbol"] = sym
            df["currency"] = ccy
            df = df[["symbol", "ex_date", "amount", "currency"]]
            con.execute("DELETE FROM dividends WHERE symbol = ?", [sym])
            con.register("d", df); con.execute("INSERT INTO dividends SELECT * FROM d"); con.unregister("d")
            _audit(jsonl, kind="dividends", symbol=sym, status="ok", rows=int(len(df)))
            time.sleep(sleep_s)
    finally:
        jsonl.close(); con.close()


# ----------------------------------------------------------------- splits
def refresh_splits(*, sleep_s: float = 1.5) -> None:
    duckdb_store.init_db()
    con = duckdb.connect(str(DUCKDB_PATH))
    jsonl = jsonl_path("market_scrape").open("a", encoding="utf-8")
    try:
        for sym, ccy in _held_symbols():
            yfsym = _yf_symbol(sym, ccy)
            log.info("Splits %s", yfsym)
            try:
                t = _ticker(yfsym)
                ser = t.splits
            except Exception as e:
                _audit(jsonl, kind="splits", symbol=sym, status="fail", err=str(e))
                continue
            if ser is None or ser.empty:
                _audit(jsonl, kind="splits", symbol=sym, status="empty")
                time.sleep(sleep_s); continue
            df = ser.reset_index()
            df.columns = ["split_date", "ratio"]
            df["split_date"] = pd.to_datetime(df["split_date"]).dt.date
            df["symbol"] = sym
            df = df[["symbol", "split_date", "ratio"]]
            con.execute("DELETE FROM splits WHERE symbol = ?", [sym])
            con.register("d", df); con.execute("INSERT INTO splits SELECT * FROM d"); con.unregister("d")
            _audit(jsonl, kind="splits", symbol=sym, status="ok", rows=int(len(df)))
            time.sleep(sleep_s)
    finally:
        jsonl.close(); con.close()


# ------------------------------------------------------------- financials
_FIN_FIELDS = {
    "Total Revenue": "revenue",
    "Gross Profit": "gross_profit",
    "Operating Income": "operating_income",
    "Net Income": "net_income",
    "Basic EPS": "eps_basic",
    "Diluted EPS": "eps_diluted",
    "EBITDA": "ebitda",
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": "total_liab",
    "Common Stock Equity": "total_equity",
    "Cash And Cash Equivalents": "cash_and_equiv",
    "Long Term Debt": "long_term_debt",
    "Operating Cash Flow": "op_cash_flow",
    "Free Cash Flow": "free_cash_flow",
    "Diluted Average Shares": "shares_diluted",
}


def _financials_frame(t, freq: str) -> pd.DataFrame:
    """freq = 'q' (quarterly) | 'a' (annual). Returns one row per period_end."""
    inc = t.quarterly_income_stmt if freq == "q" else t.income_stmt
    bal = t.quarterly_balance_sheet if freq == "q" else t.balance_sheet
    cf = t.quarterly_cashflow if freq == "q" else t.cashflow
    frames = [df for df in (inc, bal, cf) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame()
    # Align on column index (period_end)
    period_ends = sorted({c for df in frames for c in df.columns}, reverse=True)
    rows = []
    for pe in period_ends:
        row: dict = {"period_end": pd.to_datetime(pe).date()}
        for df in frames:
            if pe not in df.columns:
                continue
            for src, dst in _FIN_FIELDS.items():
                if src in df.index:
                    val = df.at[src, pe]
                    if pd.notna(val):
                        row[dst] = float(val)
        rows.append(row)
    return pd.DataFrame(rows)


def refresh_financials(*, sleep_s: float = 2.0) -> None:
    duckdb_store.init_db()
    con = duckdb.connect(str(DUCKDB_PATH))
    jsonl = jsonl_path("market_scrape").open("a", encoding="utf-8")
    try:
        for sym, ccy in _held_symbols():
            yfsym = _yf_symbol(sym, ccy)
            log.info("Financials %s", yfsym)
            try:
                t = _ticker(yfsym)
                qf = _financials_frame(t, "q")
                af = _financials_frame(t, "a")
            except Exception as e:
                _audit(jsonl, kind="financials", symbol=sym, status="fail", err=str(e))
                continue
            for label, df, table, cols in [
                ("financials_quarterly", qf, "financials_quarterly",
                 ["symbol", "period_end", "fiscal_year", "fiscal_q", "revenue",
                  "gross_profit", "operating_income", "net_income", "eps_basic",
                  "eps_diluted", "ebitda", "total_assets", "total_liab",
                  "total_equity", "cash_and_equiv", "long_term_debt",
                  "op_cash_flow", "free_cash_flow", "shares_diluted"]),
                ("financials_annual", af, "financials_annual",
                 ["symbol", "period_end", "fiscal_year", "revenue",
                  "gross_profit", "operating_income", "net_income", "eps_basic",
                  "eps_diluted", "ebitda", "total_assets", "total_liab",
                  "total_equity", "op_cash_flow", "free_cash_flow",
                  "shares_diluted"]),
            ]:
                if df.empty:
                    _audit(jsonl, kind=label, symbol=sym, status="empty")
                    continue
                df = df.copy()
                df["symbol"] = sym
                df["fiscal_year"] = df["period_end"].apply(lambda d: d.year)
                if "fiscal_q" in cols:
                    df["fiscal_q"] = df["period_end"].apply(
                        lambda d: (d.month - 1) // 3 + 1)
                for c in cols:
                    if c not in df.columns:
                        df[c] = None
                df = df[cols]
                con.execute(f"DELETE FROM {table} WHERE symbol = ?", [sym])
                con.register("d", df)
                con.execute(f"INSERT INTO {table} SELECT * FROM d")
                con.unregister("d")
                _audit(jsonl, kind=label, symbol=sym, status="ok", rows=int(len(df)))
            time.sleep(sleep_s)
    finally:
        jsonl.close(); con.close()


# --------------------------------------------------------------- earnings
def refresh_earnings(*, sleep_s: float = 1.5) -> None:
    duckdb_store.init_db()
    con = duckdb.connect(str(DUCKDB_PATH))
    jsonl = jsonl_path("market_scrape").open("a", encoding="utf-8")
    try:
        for sym, ccy in _held_symbols():
            yfsym = _yf_symbol(sym, ccy)
            log.info("Earnings %s", yfsym)
            try:
                t = _ticker(yfsym)
                df = t.earnings_dates
            except Exception as e:
                _audit(jsonl, kind="earnings", symbol=sym, status="fail", err=str(e))
                continue
            if df is None or df.empty:
                _audit(jsonl, kind="earnings", symbol=sym, status="empty")
                time.sleep(sleep_s); continue
            df = df.reset_index()
            # yfinance columns vary; normalize
            cols = {c.lower(): c for c in df.columns}
            date_col = cols.get("earnings date") or df.columns[0]
            est_col = cols.get("eps estimate")
            act_col = cols.get("reported eps")
            sur_col = cols.get("surprise(%)")
            out = pd.DataFrame({
                "symbol": sym,
                "report_date": pd.to_datetime(df[date_col], errors="coerce").dt.date,
                "fiscal_year": pd.to_datetime(df[date_col], errors="coerce").dt.year,
                "fiscal_q": pd.to_datetime(df[date_col], errors="coerce").dt.month
                                .apply(lambda m: ((m - 1) // 3 + 1) if pd.notna(m) else None),
                "eps_est": df[est_col] if est_col else None,
                "eps_actual": df[act_col] if act_col else None,
                "surprise": df[sur_col] if sur_col else None,
            })
            out = out.dropna(subset=["report_date"]).drop_duplicates("report_date")
            con.execute("DELETE FROM earnings_events WHERE symbol = ?", [sym])
            con.register("d", out); con.execute("INSERT INTO earnings_events SELECT * FROM d"); con.unregister("d")
            _audit(jsonl, kind="earnings", symbol=sym, status="ok", rows=int(len(out)))
            time.sleep(sleep_s)
    finally:
        jsonl.close(); con.close()


# ---------------------------------------------------------------------- FX
def refresh_fx(*, lookback_years: int = 15, sleep_s: float = 1.5) -> None:
    """Daily USD/CAD rates (and inverse)."""
    import yfinance as yf
    duckdb_store.init_db()
    con = duckdb.connect(str(DUCKDB_PATH))
    jsonl = jsonl_path("market_scrape").open("a", encoding="utf-8")
    start = (datetime.utcnow() - timedelta(days=365 * lookback_years)).date().isoformat()
    try:
        for pair, base, quote in [("USDCAD=X", "USD", "CAD"),
                                  ("CADUSD=X", "CAD", "USD")]:
            log.info("FX %s", pair)
            try:
                df = yf.Ticker(pair).history(start=start, interval="1d", auto_adjust=False)
            except Exception as e:
                _audit(jsonl, kind="fx", pair=pair, status="fail", err=str(e))
                continue
            if df is None or df.empty:
                _audit(jsonl, kind="fx", pair=pair, status="empty"); continue
            df = df.reset_index()
            out = pd.DataFrame({
                "base": base,
                "quote": quote,
                "rate_date": pd.to_datetime(df["Date"]).dt.date,
                "rate": df["Close"],
            }).dropna()
            con.execute("DELETE FROM fx_rates WHERE base=? AND quote=?", [base, quote])
            con.register("d", out); con.execute("INSERT INTO fx_rates SELECT * FROM d"); con.unregister("d")
            _audit(jsonl, kind="fx", pair=pair, status="ok", rows=int(len(out)))
            time.sleep(sleep_s)
    finally:
        jsonl.close(); con.close()

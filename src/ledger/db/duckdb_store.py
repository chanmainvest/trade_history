"""DuckDB schema for market and fundamentals data."""
from __future__ import annotations

import duckdb
from pathlib import Path

from ..config import DUCKDB_PATH

DDL = """
CREATE TABLE IF NOT EXISTS daily_prices (
    symbol      VARCHAR NOT NULL,
    exchange    VARCHAR,
    currency    VARCHAR,
    trade_date  DATE NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    adj_close   DOUBLE,
    volume      BIGINT,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS dividends (
    symbol      VARCHAR NOT NULL,
    ex_date     DATE NOT NULL,
    amount      DOUBLE,
    currency    VARCHAR,
    PRIMARY KEY (symbol, ex_date)
);

CREATE TABLE IF NOT EXISTS splits (
    symbol      VARCHAR NOT NULL,
    split_date  DATE NOT NULL,
    ratio       DOUBLE,
    PRIMARY KEY (symbol, split_date)
);

CREATE TABLE IF NOT EXISTS option_implied_vol (
    symbol      VARCHAR NOT NULL,
    trade_date  DATE NOT NULL,
    iv_30d      DOUBLE,
    iv_60d      DOUBLE,
    iv_90d      DOUBLE,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    base        VARCHAR NOT NULL,
    quote       VARCHAR NOT NULL,
    rate_date   DATE NOT NULL,
    rate        DOUBLE NOT NULL,
    PRIMARY KEY (base, quote, rate_date)
);

CREATE TABLE IF NOT EXISTS financials_quarterly (
    symbol         VARCHAR NOT NULL,
    period_end     DATE NOT NULL,
    fiscal_year    INTEGER,
    fiscal_q       INTEGER,
    revenue        DOUBLE,
    gross_profit   DOUBLE,
    operating_income DOUBLE,
    net_income     DOUBLE,
    eps_basic      DOUBLE,
    eps_diluted    DOUBLE,
    ebitda         DOUBLE,
    total_assets   DOUBLE,
    total_liab     DOUBLE,
    total_equity   DOUBLE,
    cash_and_equiv DOUBLE,
    long_term_debt DOUBLE,
    op_cash_flow   DOUBLE,
    free_cash_flow DOUBLE,
    shares_diluted DOUBLE,
    PRIMARY KEY (symbol, period_end)
);

CREATE TABLE IF NOT EXISTS financials_annual (
    symbol         VARCHAR NOT NULL,
    period_end     DATE NOT NULL,
    fiscal_year    INTEGER,
    revenue        DOUBLE,
    gross_profit   DOUBLE,
    operating_income DOUBLE,
    net_income     DOUBLE,
    eps_basic      DOUBLE,
    eps_diluted    DOUBLE,
    ebitda         DOUBLE,
    total_assets   DOUBLE,
    total_liab     DOUBLE,
    total_equity   DOUBLE,
    op_cash_flow   DOUBLE,
    free_cash_flow DOUBLE,
    shares_diluted DOUBLE,
    PRIMARY KEY (symbol, period_end)
);

CREATE TABLE IF NOT EXISTS earnings_events (
    symbol      VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    fiscal_year INTEGER,
    fiscal_q    INTEGER,
    eps_est     DOUBLE,
    eps_actual  DOUBLE,
    surprise    DOUBLE,
    PRIMARY KEY (symbol, report_date)
);

CREATE TABLE IF NOT EXISTS scrape_log (
    symbol      VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,
    fetched_at  TIMESTAMP NOT NULL,
    status      VARCHAR NOT NULL,
    note        VARCHAR
);
"""


def connect(path: Path | str = DUCKDB_PATH) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def init_db(path: Path | str = DUCKDB_PATH) -> None:
    con = connect(path)
    try:
        con.execute(DDL)
    finally:
        con.close()

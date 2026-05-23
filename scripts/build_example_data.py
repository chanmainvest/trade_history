"""Build the example_data SQLite + DuckDB from the portfolio_dashboard sample.

Run with:

    LEDGER_PROFILE=example uv run python scripts/build_example_data.py

This recreates `example_data/data/ledger.sqlite` and `example_data/data/market.duckdb`
from scratch. It populates two synthetic accounts (TD Direct Investing and
Interactive Brokers) with:
  * Today's holdings = the portfolio_dashboard sample sheet.
  * A back-story of ~5 years of fictional buys/sells leading up to that state.
  * A few option opening/closing events to demonstrate option tracking.

The narrative is entirely fictional and meant to look plausible against
real market history (covid recovery, AI rally, 2023 bond pain, etc.).
"""
from __future__ import annotations

import math
import os
import sqlite3
from datetime import date, timedelta

import duckdb

# Force the example profile *before* importing ledger.config.
os.environ["LEDGER_PROFILE"] = "example"

from ledger import config  # noqa: E402
from ledger.db import duckdb_store  # noqa: E402
from ledger.db import sqlite as sqlite_db

# --------------------------------------------------------------------------- holdings
# Current holdings as of 2026-05-11 (from sample_portfolio.xlsx).
# (account_code, symbol, asset_type, currency, shares, price, sector)
HOLDINGS = [
    ("TD",  "SPY",  "etf",         "USD",  100.0,  739.17, "Index"),
    ("TD",  "QQQ",  "etf",         "USD",  100.0,  708.93, "Index"),
    ("TD",  "XIU",  "etf",         "CAD", 2000.0,   49.82, "Index"),
    ("TD",  "TLT",  "etf",         "USD",  800.0,   83.66, "Bonds"),
    ("TD",  "GBUG", "etf",         "USD", 1000.0,   44.39, "Gold"),
    ("TD",  "FNV",  "equity",      "CAD",  200.0,  310.03, "Metals"),
    ("TD",  "WPM",  "equity",      "CAD",  400.0,  179.39, "Metals"),
    ("TD",  "CCO",  "equity",      "CAD",  500.0,  147.99, "Uranium"),
    ("TD",  "NVDA", "equity",      "USD",  200.0,  225.32, "Tech"),
    ("TD",  "AMD",  "equity",      "USD",  200.0,  424.10, "Tech"),
    ("TD",  "GOOG", "equity",      "USD",  150.0,  393.32, "Tech"),
    ("TD",  "XOM",  "equity",      "USD",  200.0,  157.92, "Energy"),
    ("TD",  "CVX",  "equity",      "USD",  200.0,  191.10, "Energy"),
    ("IB",  "IBIT", "etf",         "USD", 1000.0,   44.82, "Crypto"),
    ("IB",  "ETHA", "etf",         "USD",  500.0,   16.76, "Crypto"),
]

CASH = [
    ("TD", "CAD",   500.0),
    ("TD", "USD",  3000.0),
    ("IB", "CAD",  2000.0),
    ("IB", "USD",     0.0),
]

AS_OF = "2026-05-11"
PERIOD_START = "2026-05-01"
SNAPSHOT_DATES = [
    "2021-12-31",
    "2022-12-30",
    "2023-12-29",
    "2024-12-31",
    "2025-12-31",
    AS_OF,
]

# Backstory transactions: (date, account_code, txn_type, symbol, asset_type,
#                          currency, qty, price, description)
# Quantities are SIGNED: + buy/long, - sell/short. net_amount sign matches.
TRADES = [
    # 2021 — covid recovery, rate cuts
    ("2021-02-10", "TD", "buy",      "XIU",  "etf",    "CAD", 1500.0,  29.10, "Bought iShares TSX 60 — full position"),
    ("2021-03-04", "TD", "buy",      "SPY",  "etf",    "USD",   50.0, 387.00, "Started SPY core position"),
    ("2021-05-18", "TD", "buy",      "QQQ",  "etf",    "USD",   40.0, 334.00, "Nasdaq core add"),
    ("2021-07-08", "TD", "buy",      "NVDA", "equity", "USD",  300.0,  18.10, "Pre-split NVDA conviction buy"),
    ("2021-11-22", "TD", "buy",      "AMD",  "equity", "USD",  100.0, 158.30, "AMD on dip after Zen 3"),
    # 2022 — inflation, bond pain, energy rally
    ("2022-02-14", "TD", "buy",      "XOM",  "equity", "USD",  200.0,  77.55, "Energy hedge against inflation"),
    ("2022-02-14", "TD", "buy",      "CVX",  "equity", "USD",  200.0, 137.20, "CVX paired with XOM"),
    ("2022-06-21", "TD", "buy",      "TLT",  "etf",    "USD",  300.0, 113.20, "Bought TLT at first 10y yield spike"),
    ("2022-10-12", "TD", "buy",      "TLT",  "etf",    "USD",  300.0,  94.30, "Added TLT into bond panic"),
    ("2022-11-02", "TD", "buy",      "CCO",  "equity", "CAD",  300.0,  35.40, "Cameco uranium thesis"),
    # 2023 — AI rally, gold leg up
    ("2023-01-30", "TD", "buy",      "GOOG", "equity", "USD",  150.0,  97.20, "Google after Bard event dip"),
    ("2023-03-13", "TD", "buy",      "FNV",  "equity", "CAD",  100.0, 175.20, "Franco-Nevada — gold royalty"),
    ("2023-05-09", "TD", "buy",      "WPM",  "equity", "CAD",  200.0,  56.10, "Wheaton silver/gold"),
    ("2023-06-02", "TD", "sell",     "NVDA", "equity", "USD", -100.0, 401.20, "Trimmed NVDA after Q1'24 print"),
    ("2023-08-21", "TD", "buy",      "QQQ",  "etf",    "USD",   60.0, 367.80, "Added to QQQ on AI pullback"),
    # 2024 — gold + uranium runs, bitcoin ETF launch
    ("2024-01-12", "IB", "buy",      "IBIT", "etf",    "USD",  600.0,  26.50, "IBIT — spot BTC ETF launch"),
    ("2024-04-04", "TD", "buy",      "WPM",  "equity", "CAD",  200.0,  62.40, "Doubled WPM into silver squeeze"),
    ("2024-04-29", "TD", "buy",      "CCO",  "equity", "CAD",  200.0,  68.10, "More Cameco — uranium spot $90"),
    ("2024-07-25", "TD", "buy",      "FNV",  "equity", "CAD",  100.0, 178.40, "Added FNV on Cobre Panama news"),
    ("2024-09-10", "TD", "buy",      "GBUG", "etf",    "USD",  400.0,  29.20, "Bitcoin-related ETF starter"),
    # 2025 — gold to new highs, regional bank wobble
    ("2025-01-08", "IB", "buy",      "ETHA", "etf",    "USD",  500.0,  19.85, "ETH ETF — starter"),
    ("2025-02-19", "TD", "buy",      "TLT",  "etf",    "USD",  200.0,  88.50, "TLT into rate-cut hopes"),
    ("2025-05-15", "TD", "buy",      "GBUG", "etf",    "USD",  600.0,  31.20, "Topped up GBUG"),
    ("2025-08-06", "TD", "sell",     "NVDA", "equity", "USD", -100.0, 165.40, "Trimmed NVDA on parabolic move"),
    ("2025-11-04", "IB", "buy",      "IBIT", "etf",    "USD",  400.0,  41.20, "Added IBIT after halving cycle"),
    # 2026 — current year
    ("2026-01-22", "TD", "buy",      "SPY",  "etf",    "USD",   50.0, 689.40, "SPY rebalance into year"),
    ("2026-03-11", "TD", "buy",      "QQQ",  "etf",    "USD",   60.0, 651.20, "QQQ added on Fed pause"),
    # --- A few option events (quantities are option contracts, not underlying shares)
    ("2026-04-02", "TD", "option_sell_to_open", "FCX", "option", "USD", -50.0, 1.20,
        "PUT FCX JUN 18 2026 40.00 — sold 50 contracts"),
    ("2026-04-02", "TD", "option_buy_to_open",  "FCX", "option", "USD",  20.0, 0.45,
        "PUT FCX JUN 18 2026 45.00 — bought 20 contracts"),
    # SLV credit spread
    ("2026-04-15", "TD", "option_sell_to_open", "SLV", "option", "USD", -50.0, 0.95,
        "PUT SLV JUN 18 2026 63.00 — short premium"),
    ("2026-04-15", "TD", "option_buy_to_open",  "SLV", "option", "USD",  25.0, 0.30,
        "PUT SLV JUN 18 2026 70.00 — long protective"),
    # AAPL long-dated ladder
    ("2026-04-22", "TD", "option_buy_to_open",  "AAPL", "option", "USD", 12.0, 4.20,
        "PUT AAPL JAN 15 2027 150.00 — long protection"),
    ("2026-04-22", "TD", "option_sell_to_open", "AAPL", "option", "USD", -9.0, 14.80,
        "PUT AAPL JAN 15 2027 200.00 — short premium"),
    ("2026-04-22", "TD", "option_buy_to_open",  "AAPL", "option", "USD",  3.0, 32.10,
        "PUT AAPL JAN 15 2027 250.00 — deep long"),
    # Dividends sprinkled in
    ("2025-09-19", "TD", "dividend", "XOM",  "equity", "USD",    None,  None,  "XOM Q3 dividend $0.95 x 200"),
    ("2025-09-19", "TD", "dividend", "CVX",  "equity", "USD",    None,  None,  "CVX Q3 dividend $1.63 x 200"),
    ("2025-12-12", "TD", "dividend", "XIU",  "etf",    "CAD",    None,  None,  "XIU Q4 distribution"),
]

EXTRA_PRICE_SEEDS = {
    "AAPL": ("USD", 200.0),
    "FCX": ("USD", 42.0),
    "SLV": ("USD", 65.0),
}


def _sample_symbols() -> list[str]:
    symbols = {row[1] for row in OPTION_POSITIONS}
    symbols |= {sym for _, sym, *_ in HOLDINGS}
    symbols |= {row[3] for row in TRADES if row[3]}
    symbols |= set(EXTRA_PRICE_SEEDS)
    return sorted(symbols)


def _seed_market_data(duck_path) -> None:
    symbols = _sample_symbols()
    real_path = config.ROOT / "data" / "market.duckdb"
    target = duckdb.connect(str(duck_path))
    try:
        if real_path.exists() and real_path.resolve() != duck_path.resolve():
            escaped = str(real_path).replace("'", "''")
            target.execute(f"ATTACH '{escaped}' AS real_market (READ_ONLY)")
            ph = ",".join("?" * len(symbols))
            table_columns = {
                "daily_prices": "symbol, exchange, currency, trade_date, open, high, low, close, adj_close, volume",
                "financials_quarterly": "symbol, period_end, fiscal_year, fiscal_q, revenue, gross_profit, operating_income, net_income, eps_basic, eps_diluted, ebitda, total_assets, total_liab, total_equity, cash_and_equiv, long_term_debt, op_cash_flow, free_cash_flow, shares_diluted",
                "financials_annual": "symbol, period_end, fiscal_year, revenue, gross_profit, operating_income, net_income, eps_basic, eps_diluted, ebitda, total_assets, total_liab, total_equity, op_cash_flow, free_cash_flow, shares_diluted",
                "dividends": "symbol, ex_date, amount, currency",
                "splits": "symbol, split_date, ratio",
                "earnings_events": "symbol, report_date, fiscal_year, fiscal_q, eps_est, eps_actual, surprise",
            }
            try:
                for table, columns in table_columns.items():
                    target.execute(
                        f"""
                        INSERT OR REPLACE INTO {table} ({columns})
                        SELECT {columns} FROM real_market.{table}
                         WHERE symbol IN ({ph})
                        """,
                        symbols,
                    )
                target.execute(
                    """
                    INSERT OR REPLACE INTO fx_rates(base, quote, rate_date, rate)
                    SELECT base, quote, rate_date, rate FROM real_market.fx_rates
                    """
                )
            finally:
                target.execute("DETACH real_market")

        # Add local profile metadata from the synthetic holdings so sector UI is useful.
        for _, sym, atype, _ccy, _shares, _price, sector in HOLDINGS:
            target.execute(
                """
                INSERT OR REPLACE INTO symbol_profiles(symbol, short_name, sector, industry, quote_type, fetched_at)
                VALUES (?, ?, ?, ?, ?, current_timestamp)
                """,
                [sym, sym, sector, sector, atype.upper()],
            )

        _fill_missing_prices(target, symbols)
    finally:
        target.close()


def _fill_missing_prices(con: duckdb.DuckDBPyConnection, symbols: list[str]) -> None:
    price_seed = {sym: (ccy, price) for _, sym, _atype, ccy, _shares, price, _sector in HOLDINGS}
    price_seed.update(EXTRA_PRICE_SEEDS)
    start = date(2021, 1, 4)
    end = date.fromisoformat(AS_OF)
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    for symbol in symbols:
        count = con.execute("SELECT COUNT(*) FROM daily_prices WHERE symbol = ?", [symbol]).fetchone()[0]
        if count:
            continue
        currency, current_price = price_seed.get(symbol, ("USD", 100.0))
        rows = []
        for index, day in enumerate(days):
            progress = index / max(1, len(days) - 1)
            wave = 1 + 0.055 * math.sin(index / 19 + len(symbol)) + 0.025 * math.sin(index / 47)
            close = max(0.5, current_price * (0.58 + 0.42 * progress) * wave)
            open_ = close * (1 + 0.004 * math.sin(index / 11))
            high = max(open_, close) * 1.012
            low = min(open_, close) * 0.988
            volume = int(500_000 + 25_000 * (1 + math.sin(index / 13)) * max(1, len(symbol)))
            rows.append((symbol, None, currency, day, open_, high, low, close, close, volume))
        con.executemany(
            """
            INSERT OR REPLACE INTO daily_prices
            (symbol, exchange, currency, trade_date, open, high, low, close, adj_close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _estimate_snapshot_price(symbol: str, snap_date: str, current_price: float) -> float:
    start = date(2021, 1, 1)
    end = date.fromisoformat(AS_OF)
    current = date.fromisoformat(snap_date)
    progress = max(0.0, min(1.0, (current - start).days / max(1, (end - start).days)))
    return round(current_price * (0.58 + 0.42 * progress), 2)
# Dividend net_amount (positive, cash credit)
DIV_AMOUNTS = {
    ("2025-09-19", "XOM"): 190.00,
    ("2025-09-19", "CVX"): 326.00,
    ("2025-12-12", "XIU"): 480.00,
}

# Options expirations (June 2026) — these are currently OPEN as of as_of date, so
# we record them in position_snapshots as well.
OPTION_POSITIONS = [
    # (account, root, currency, expiry, strike, cp, qty, mark_price, mv)
    ("TD", "FCX",  "USD", "2026-06-18", 40.0,  "PUT", -50, 0.36, -1800.0),
    ("TD", "FCX",  "USD", "2026-06-18", 45.0,  "PUT",  20, 0.00,     0.0),
    ("TD", "SLV",  "USD", "2026-06-18", 63.0,  "PUT", -50, 0.55, -2775.0),
    ("TD", "SLV",  "USD", "2026-06-18", 70.0,  "PUT",  25, 0.00,     0.0),
    ("TD", "AAPL", "USD", "2027-01-15", 150.0, "PUT",  12, 0.00,     0.0),
    ("TD", "AAPL", "USD", "2027-01-15", 200.0, "PUT",  -9, 2.00, -1800.0),
    ("TD", "AAPL", "USD", "2027-01-15", 250.0, "PUT",   3, 0.00,     0.0),
]


def build() -> None:
    # Wipe & re-init
    db_path = config.SQLITE_PATH
    duck_path = config.DUCKDB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    if duck_path.exists():
        duck_path.unlink()
    sqlite_db.init_db()
    duckdb_store.init_db()
    _seed_market_data(duck_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # ----- Institutions / Accounts
    cur.execute("INSERT INTO institutions(code, display_name) VALUES(?,?)",
                ("TD_DI", "TD Direct Investing"))
    cur.execute("INSERT INTO institutions(code, display_name) VALUES(?,?)",
                ("IBKR",  "Interactive Brokers"))
    td_inst = cur.execute("SELECT institution_id FROM institutions WHERE code='TD_DI'").fetchone()[0]
    ib_inst = cur.execute("SELECT institution_id FROM institutions WHERE code='IBKR'").fetchone()[0]

    cur.execute(
        "INSERT INTO accounts(institution_id, account_number, account_type, "
        "nickname, base_currency, opened_on) VALUES(?,?,?,?,?,?)",
        (td_inst, "555-01234-1", "Margin", "TD Joint", "CAD", "2021-01-15"),
    )
    cur.execute(
        "INSERT INTO accounts(institution_id, account_number, account_type, "
        "nickname, base_currency, opened_on) VALUES(?,?,?,?,?,?)",
        (ib_inst, "U7891234",   "Margin", "IBKR USD", "USD", "2023-11-04"),
    )
    accts = {
        "TD": cur.execute("SELECT account_id FROM accounts WHERE institution_id=?", (td_inst,)).fetchone()[0],
        "IB": cur.execute("SELECT account_id FROM accounts WHERE institution_id=?", (ib_inst,)).fetchone()[0],
    }

    # ----- Instrument cache: get-or-create
    def instr_id(asset_type: str, symbol: str, currency: str, *,
                 option_expiry: str | None = None, option_strike: float | None = None,
                 option_type: str | None = None, name: str | None = None,
                 exchange: str | None = None) -> int:
        row = cur.execute(
            "SELECT instrument_id FROM instruments WHERE asset_type=? AND symbol=? "
            "AND currency=? AND IFNULL(option_expiry,'')=IFNULL(?,'') "
            "AND IFNULL(option_strike,0)=IFNULL(?,0) "
            "AND IFNULL(option_type,'')=IFNULL(?,'')",
            (asset_type, symbol, currency, option_expiry, option_strike, option_type),
        ).fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO instruments(asset_type, symbol, exchange, currency, name, "
            "option_root, option_expiry, option_strike, option_type, option_multiplier) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (asset_type, symbol, exchange, currency, name or symbol,
             symbol if asset_type == "option" else None,
             option_expiry, option_strike, option_type,
             100 if asset_type == "option" else None),
        )
        return cur.lastrowid

    # ----- Synthetic monthly/annual checkpoint statements for charts
    stmt_id: dict[tuple[str, str], int] = {}
    for acct_code, account_id in accts.items():
        for snap_date in SNAPSHOT_DATES:
            period_start = f"{snap_date[:8]}01"
            cur.execute(
                "INSERT INTO source_files(relpath, sha256, size_bytes, page_count, "
                "is_image_only, parser_name, parser_version, parsed_at, parse_status) "
                "VALUES(?,?,?,?,?,?,?,datetime('now'),'ok')",
                (f"example_data/Statements/{acct_code}/{snap_date}.example",
                 None, 0, 1, 0, "example-builder", "1.0"),
            )
            sf_id = cur.lastrowid
            cur.execute(
                "INSERT INTO statements(source_file_id, account_id, period_start, "
                "period_end, statement_type) VALUES(?,?,?,?,?)",
                (sf_id, account_id, period_start, snap_date, "monthly"),
            )
            stmt_id[(acct_code, snap_date)] = cur.lastrowid

    # ----- Position snapshots: equities + ETFs
    for acct, sym, atype, ccy, shares, price, _sector in HOLDINGS:
        iid = instr_id(atype, sym, ccy)
        mv = shares * price
        cur.execute(
            "INSERT INTO position_snapshots(statement_id, account_id, as_of_date, "
            "instrument_id, quantity, avg_cost, book_value, market_price, "
            "market_value, unrealized_pnl, currency) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (stmt_id[(acct, AS_OF)], accts[acct], AS_OF, iid,
             shares, None, None, price, mv, None, ccy),
        )

    # Option snapshots
    for acct, root, ccy, expiry, strike, cp, qty, mark, mv in OPTION_POSITIONS:
        iid = instr_id("option", root, ccy,
                       option_expiry=expiry, option_strike=strike, option_type=cp,
                       name=f"{cp} {root} {expiry} {strike:.2f}")
        cur.execute(
            "INSERT INTO position_snapshots(statement_id, account_id, as_of_date, "
            "instrument_id, quantity, avg_cost, book_value, market_price, "
            "market_value, unrealized_pnl, currency) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (stmt_id[(acct, AS_OF)], accts[acct], AS_OF, iid,
             qty, None, None, mark, mv, None, ccy),
        )

    # Cash balances
    for acct, ccy, amt in CASH:
        cur.execute(
            "INSERT INTO cash_balances(statement_id, account_id, as_of_date, "
            "currency, opening_balance, closing_balance) VALUES(?,?,?,?,?,?)",
            (stmt_id[(acct, AS_OF)], accts[acct], AS_OF, ccy, amt, amt),
        )

    current_prices = {sym: price for _, sym, _atype, _ccy, _shares, price, _sector in HOLDINGS}
    for snap_date in SNAPSHOT_DATES[:-1]:
        quantities: dict[tuple[str, str, str, str], float] = {}
        for trade_date, acct, ttype, sym, atype, ccy, qty, _price, _desc in TRADES:
            if not qty or atype == "option" or ttype == "dividend" or trade_date > snap_date:
                continue
            key = (acct, sym, atype, ccy)
            quantities[key] = quantities.get(key, 0.0) + qty
        for (acct, sym, atype, ccy), qty in sorted(quantities.items()):
            if abs(qty) <= 1e-9:
                continue
            iid = instr_id(atype, sym, ccy)
            price = _estimate_snapshot_price(sym, snap_date, current_prices.get(sym, 100.0))
            mv = qty * price
            cur.execute(
                "INSERT INTO position_snapshots(statement_id, account_id, as_of_date, "
                "instrument_id, quantity, avg_cost, book_value, market_price, "
                "market_value, unrealized_pnl, currency) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (stmt_id[(acct, snap_date)], accts[acct], snap_date, iid,
                 qty, None, None, price, mv, None, ccy),
            )
        progress = (date.fromisoformat(snap_date) - date(2021, 1, 1)).days / (
            date.fromisoformat(AS_OF) - date(2021, 1, 1)
        ).days
        for acct, ccy, amt in CASH:
            balance = round(amt * (0.35 + 0.65 * progress), 2)
            cur.execute(
                "INSERT INTO cash_balances(statement_id, account_id, as_of_date, "
                "currency, opening_balance, closing_balance) VALUES(?,?,?,?,?,?)",
                (stmt_id[(acct, snap_date)], accts[acct], snap_date, ccy, balance, balance),
            )

    # ----- Transactions
    for row in TRADES:
        trade_date, acct, ttype, sym, atype, ccy, qty, price, desc = row
        if atype == "option":
            # Description carries the option meta; parse inline.
            # We expect "...<CALL|PUT> <root> <MON DD YYYY> <strike>..."
            parts = desc.split()
            cp = parts[0] if parts[0] in ("CALL", "PUT") else "PUT"
            # find pattern
            import re as _re
            m = _re.search(r"(CALL|PUT)\s+([A-Z]{1,6})\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{4})\s+([\d.]+)", desc)
            if not m:
                continue
            cp, root, mon, dd, yr, strike_s = m.groups()
            mon_map = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                       "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
            expiry = date(int(yr), mon_map[mon], int(dd)).isoformat()
            iid = instr_id("option", root, ccy,
                           option_expiry=expiry, option_strike=float(strike_s),
                           option_type=cp,
                           name=f"{cp} {root} {expiry} {strike_s}")
            net = -(qty * price * 100) if price else None  # buy = cash out
        elif ttype == "dividend":
            iid = instr_id(atype, sym, ccy)
            net = DIV_AMOUNTS.get((trade_date, sym), 0.0)
        else:
            iid = instr_id(atype, sym, ccy)
            net = -(qty * price) if (qty and price) else None
        cur.execute(
            "INSERT INTO transactions(account_id, statement_id, trade_date, "
            "txn_type, instrument_id, quantity, price, net_amount, currency, "
            "description, raw_line) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (accts[acct], stmt_id[(acct, AS_OF)], trade_date, ttype, iid,
             qty, price, net, ccy, desc, desc),
        )

    n_pos = cur.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0]
    conn.commit()
    conn.close()

    n_txn = sum(1 for _ in TRADES)
    print(f"Built example DB: {db_path}")
    print(f"  {len(accts)} accounts, {n_txn} transactions, {n_pos} positions.")
    print(f"  DuckDB: {duck_path} (seeded from real market DB where available).")


if __name__ == "__main__":
    build()

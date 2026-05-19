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

import os
import sqlite3
from datetime import date

# Force the example profile *before* importing ledger.config.
os.environ["LEDGER_PROFILE"] = "example"

from ledger import config  # noqa: E402
from ledger.db import duckdb_store, sqlite as sqlite_db  # noqa: E402


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
    # --- A few option events (FCX June 2026 PUT spread = sell 5000 @ $40 / buy 2000 @ $45)
    ("2026-04-02", "TD", "option_sell_to_open", "FCX", "option", "USD", -5000.0, 1.20,
        "PUT FCX JUN 18 2026 40.00 — sold 50 contracts"),
    ("2026-04-02", "TD", "option_buy_to_open",  "FCX", "option", "USD",  2000.0, 0.45,
        "PUT FCX JUN 18 2026 45.00 — bought 20 contracts"),
    # SLV credit spread
    ("2026-04-15", "TD", "option_sell_to_open", "SLV", "option", "USD", -5000.0, 0.95,
        "PUT SLV JUN 18 2026 63.00 — short premium"),
    ("2026-04-15", "TD", "option_buy_to_open",  "SLV", "option", "USD",  2500.0, 0.30,
        "PUT SLV JUN 18 2026 70.00 — long protective"),
    # AAPL long-dated ladder
    ("2026-04-22", "TD", "option_buy_to_open",  "AAPL", "option", "USD", 1200.0, 4.20,
        "PUT AAPL JAN 15 2027 150.00 — long protection"),
    ("2026-04-22", "TD", "option_sell_to_open", "AAPL", "option", "USD", -900.0, 14.80,
        "PUT AAPL JAN 15 2027 200.00 — short premium"),
    ("2026-04-22", "TD", "option_buy_to_open",  "AAPL", "option", "USD",  300.0, 32.10,
        "PUT AAPL JAN 15 2027 250.00 — deep long"),
    # Dividends sprinkled in
    ("2025-09-19", "TD", "dividend", "XOM",  "equity", "USD",    None,  None,  "XOM Q3 dividend $0.95 x 200"),
    ("2025-09-19", "TD", "dividend", "CVX",  "equity", "USD",    None,  None,  "CVX Q3 dividend $1.63 x 200"),
    ("2025-12-12", "TD", "dividend", "XIU",  "etf",    "CAD",    None,  None,  "XIU Q4 distribution"),
]
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

    # ----- A single source file + statement per account, dated 2026-05-11
    AS_OF = "2026-05-11"
    PERIOD_START = "2026-05-01"
    for acct_code, account_id in accts.items():
        cur.execute(
            "INSERT INTO source_files(relpath, sha256, size_bytes, page_count, "
            "is_image_only, parser_name, parser_version, parsed_at, parse_status) "
            "VALUES(?,?,?,?,?,?,?,datetime('now'),'ok')",
            (f"example_data/Statements/{acct_code}/2026-05.example",
             None, 0, 1, 0, "example-builder", "1.0"),
        )
        sf_id = cur.lastrowid
        cur.execute(
            "INSERT INTO statements(source_file_id, account_id, period_start, "
            "period_end, statement_type) VALUES(?,?,?,?,?)",
            (sf_id, account_id, PERIOD_START, AS_OF, "monthly"),
        )

    # Pull the statement_id per account
    stmt_id = {
        code: cur.execute(
            "SELECT statement_id FROM statements WHERE account_id=? ORDER BY period_end DESC LIMIT 1",
            (aid,),
        ).fetchone()[0]
        for code, aid in accts.items()
    }

    # ----- Position snapshots: equities + ETFs
    for acct, sym, atype, ccy, shares, price, _sector in HOLDINGS:
        iid = instr_id(atype, sym, ccy)
        mv = shares * price
        cur.execute(
            "INSERT INTO position_snapshots(statement_id, account_id, as_of_date, "
            "instrument_id, quantity, avg_cost, book_value, market_price, "
            "market_value, unrealized_pnl, currency) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (stmt_id[acct], accts[acct], AS_OF, iid,
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
            (stmt_id[acct], accts[acct], AS_OF, iid,
             qty, None, None, mark, mv, None, ccy),
        )

    # Cash balances
    for acct, ccy, amt in CASH:
        cur.execute(
            "INSERT INTO cash_balances(statement_id, account_id, as_of_date, "
            "currency, opening_balance, closing_balance) VALUES(?,?,?,?,?,?)",
            (stmt_id[acct], accts[acct], AS_OF, ccy, amt, amt),
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
            (accts[acct], stmt_id[acct], trade_date, ttype, iid,
             qty, price, net, ccy, desc, desc),
        )

    conn.commit()
    conn.close()

    n_txn = sum(1 for _ in TRADES)
    n_pos = len(HOLDINGS) + len(OPTION_POSITIONS)
    print(f"Built example DB: {db_path}")
    print(f"  {len(accts)} accounts, {n_txn} transactions, {n_pos} positions.")
    print(f"  DuckDB: {duck_path} (empty — run `ledger market` to populate).")


if __name__ == "__main__":
    build()

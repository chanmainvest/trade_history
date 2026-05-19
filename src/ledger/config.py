"""Project-wide paths and constants.

The active workspace is selected via the ``LEDGER_PROFILE`` env var:

* ``LEDGER_PROFILE=real`` (default) — real personal statements in ``Statements/``
  and the live ``data/ledger.sqlite`` + ``data/market.duckdb``.
* ``LEDGER_PROFILE=example`` — sample portfolio shipped in ``example_data/``.

You can also override individual paths with:
* ``LEDGER_DATA_DIR``      — where ``ledger.sqlite`` / ``market.duckdb`` live.
* ``LEDGER_STATEMENTS_DIR`` — the input PDF tree the ingester walks.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE = os.environ.get("LEDGER_PROFILE", "real").strip().lower()

if PROFILE == "example":
    DATA_DIR = Path(os.environ.get("LEDGER_DATA_DIR", str(ROOT / "example_data" / "data")))
    STATEMENTS_DIR = Path(os.environ.get("LEDGER_STATEMENTS_DIR", str(ROOT / "example_data" / "Statements")))
else:
    DATA_DIR = Path(os.environ.get("LEDGER_DATA_DIR", str(ROOT / "data")))
    STATEMENTS_DIR = Path(os.environ.get("LEDGER_STATEMENTS_DIR", str(ROOT / "Statements")))

LOG_DIR = ROOT / "logs"
TEXT_DUMP_DIR = DATA_DIR / "text_dumps"

SQLITE_PATH = DATA_DIR / "ledger.sqlite"
DUCKDB_PATH = DATA_DIR / "market.duckdb"

# Map of folder name on disk -> canonical institution code.
INSTITUTIONS: dict[str, str] = {
    "CIBC Imperial Service": "CIBC_IS",
    "CIBC Invest Direct": "CIBC_ID",
    "CIBC TSFA": "CIBC_TFSA",
    "HSBC direct invest": "HSBC_IDI",
    "RBC Invest Direct": "RBC_DI",
    "TD Webbroker": "TD_WB",
    # Example-data profiles
    "TD Direct Investing": "TD_DI",
    "Interactive Brokers": "IBKR",
}

for d in (DATA_DIR, LOG_DIR, TEXT_DUMP_DIR):
    d.mkdir(parents=True, exist_ok=True)


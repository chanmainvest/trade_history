"""Project-wide paths and constants."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
STATEMENTS_DIR = ROOT / "Statements"
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
}

for d in (DATA_DIR, LOG_DIR, TEXT_DUMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

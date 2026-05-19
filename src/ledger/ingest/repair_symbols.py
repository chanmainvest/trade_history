"""Repair stale transaction instruments whose symbol is a leading verb.

Early RBC parsing sometimes created instruments named BOUGHT/SOLD when the
statement line had no explicit ticker. The parser now uses name_resolver, but
existing databases need a small data repair so Transactions/Research show the
real ticker.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from ..db import sqlite as sqlite_db
from ..parsers.name_resolver import resolve_ticker, strip_leading_verbs

_MONTH = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_OPTION_DESC = re.compile(
    r"^\s*(CALL|PUT)\s+\.?([A-Z][A-Z0-9.\-]{0,8})\s+"
    r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
    r"(\d{1,2})\s+(\d{4})(?:\s+|\s*\|\s*)([\d.,]+)",
    re.IGNORECASE,
)
_OPTION_SYMBOL = re.compile(r"^(CALL|PUT)_([A-Z][A-Z0-9.]*)", re.IGNORECASE)
_CANONICAL_SYMBOL = re.compile(r"^[A-Z][A-Z0-9-]{0,8}(?:\.[A-Z]{1,3})?$")
_SHARES_RE = re.compile(r"\bON\s+(-?[\d,]+(?:\.\d+)?)\s+SHS\b", re.IGNORECASE)
_BAD_SYMBOLS = {
    "BOUGHT", "SOLD", "DIVIDEND", "DISTRIBUTION", "TRANSFER",
    "NON_RES_TAX_WITHHELD", "NON-RES_TAX_WITHHELD", "UNKNOWN",
}
_STOP_WORDS = {
    "A", "AN", "THE", "AND", "OF", "ON", "INC", "LTD", "PLC", "CORP",
    "CORPORATION", "COMPANY", "CO", "COM", "COMMON", "STOCK", "SHARES",
    "ETF", "UNIT", "UNITS", "TR", "TRUST", "FDS", "FUNDS", "FUND", "CLASS",
    "CL", "SPONSORED", "ADR", "CASH", "DIV", "REC", "PAY", "NRT",
    "UNSOLICITED", "SAME", "ACCOUNT", "WITHHELD", "TAX", "NON", "RES",
}


def _parse_option_desc(desc: str) -> tuple[str, str, str | None, float | None] | None:
    m = _OPTION_DESC.match(desc or "")
    if not m:
        return None
    opt_type, raw_root, mon, day_s, year_s, strike_s = m.groups()
    root = raw_root.upper().replace(".", "")
    if root[-1:].isdigit() and "ADJ" in (desc or "").upper():
        root = root.rstrip("0123456789") or root
    try:
        expiry = date(int(year_s), _MONTH[mon.upper()], int(day_s)).isoformat()
        strike = float(strike_s.replace(",", ""))
    except (KeyError, ValueError):
        return None
    return root, opt_type.upper(), expiry, strike


def _parse_option_symbol(symbol: str) -> tuple[str, str, str | None, float | None] | None:
    m = _OPTION_SYMBOL.match(symbol or "")
    if not m:
        return None
    opt_type, raw_root = m.groups()
    root = raw_root.upper().replace(".", "")
    if root[-1:].isdigit():
        root = root.rstrip("0123456789") or root
    return root, opt_type.upper(), None, None


def _instrument_id(conn, symbol: str, asset_type: str, currency: str, name: str | None) -> int:
    row = conn.execute(
        "SELECT instrument_id FROM instruments "
        " WHERE asset_type = ? AND symbol = ? AND currency = ? "
        "   AND option_expiry IS NULL AND option_strike IS NULL AND option_type IS NULL "
        " LIMIT 1",
        (asset_type, symbol, currency),
    ).fetchone()
    if row:
        return int(row["instrument_id"])
    cur = conn.execute(
        "INSERT INTO instruments "
        "  (asset_type, symbol, exchange, currency, name, cusip, isin, "
        "   option_root, option_expiry, option_strike, option_type) "
        "VALUES (?, ?, NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)",
        (asset_type, symbol, currency, name),
    )
    return int(cur.lastrowid)


def _display_symbol(row) -> str | None:
    option_root = row["option_root"] if "option_root" in row.keys() else None
    symbol = row["symbol"] if "symbol" in row.keys() else None
    return option_root or symbol


def _is_bad_symbol(symbol: str | None) -> bool:
    if not symbol:
        return False
    upper = symbol.upper()
    return "_" in upper or upper in _BAD_SYMBOLS or upper.startswith(("CALL_", "PUT_"))


def _is_canonical_symbol(symbol: str | None) -> bool:
    if not symbol:
        return False
    upper = symbol.upper()
    return bool(_CANONICAL_SYMBOL.match(upper)) and upper not in _BAD_SYMBOLS


def _clean_name(text: str | None) -> str:
    cleaned = strip_leading_verbs(text or "").upper()
    cleaned = re.sub(r"\|", " ", cleaned)
    cleaned = re.sub(r"[—–-]", " ", cleaned)
    cleaned = re.sub(r"\$?[\d,]+(?:\.\d+)?", " ", cleaned)
    cleaned = re.sub(r"\b(?:CASH|DIST|DIV|REC|PAY|NRT|UNSOLICITED|OPEN|CONTRACT)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _words(text: str | None) -> set[str]:
    return {
        token for token in re.findall(r"[A-Z][A-Z0-9]{1,}", _clean_name(text))
        if token not in _STOP_WORDS
    }


def _shares_from_description(description: str | None) -> float | None:
    match = _SHARES_RE.search(description or "")
    if not match:
        return None
    try:
        return abs(float(match.group(1).replace(",", "")))
    except ValueError:
        return None


def _resolved_instrument_id(conn, description: str, currency: str, fallback_name: str | None = None) -> int | None:
    resolved = resolve_ticker(strip_leading_verbs(description), currency)
    if not resolved:
        return None
    symbol, asset_type = resolved
    return _instrument_id(conn, symbol, asset_type, currency, fallback_name or strip_leading_verbs(description)[:120])


def _position_match_score(transaction, snapshot) -> int:
    description = transaction["description"] or ""
    candidate_name = snapshot["name"] or snapshot["raw_line"] or ""
    description_words = _words(description)
    candidate_words = _words(candidate_name)
    if not description_words or not candidate_words:
        return 0

    score = len(description_words & candidate_words) * 6
    cleaned_description = _clean_name(description)
    cleaned_candidate = _clean_name(candidate_name)
    if len(candidate_words) >= 2 and candidate_words.issubset(description_words):
        score += 35
    if cleaned_candidate and cleaned_candidate in cleaned_description:
        score += 45
    display_symbol = _display_symbol(snapshot)
    if display_symbol and display_symbol.upper() in description_words:
        score += 35

    transaction_quantity = transaction["quantity"]
    quantity_hint = abs(float(transaction_quantity)) if transaction_quantity is not None else _shares_from_description(description)
    snapshot_quantity = abs(float(snapshot["quantity"])) if snapshot["quantity"] is not None else None
    if quantity_hint is not None and snapshot_quantity is not None and abs(quantity_hint - snapshot_quantity) < 0.001:
        score += 25
    if transaction["currency"] == snapshot["currency"]:
        score += 8
    if _is_canonical_symbol(display_symbol):
        score += 8
    return score


def _best_snapshot_match(conn, transaction) -> tuple[int, int | None] | None:
    rows = conn.execute(
        "SELECT ps.snapshot_id, ps.instrument_id, ps.quantity, ps.currency, ps.raw_line, "
        "       inst.symbol, inst.asset_type, inst.name, inst.option_root "
        "  FROM position_snapshots ps "
        "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id "
        " WHERE ps.statement_id = ? AND ps.account_id = ? "
        "   AND inst.asset_type IN ('equity','etf','mutual_fund','bond')",
        (transaction["statement_id"], transaction["account_id"]),
    ).fetchall()
    best_score = 0
    best_row = None
    for snapshot in rows:
        score = _position_match_score(transaction, snapshot)
        if score > best_score:
            best_score = score
            best_row = snapshot
    if best_row is None or best_score < 35:
        return None

    currency = transaction["currency"] or best_row["currency"] or "USD"
    resolved_id = _resolved_instrument_id(
        conn,
        " ".join(x for x in [transaction["description"], best_row["name"], best_row["raw_line"]] if x),
        currency,
        best_row["name"],
    )
    if resolved_id is not None:
        return resolved_id, int(best_row["snapshot_id"])
    display_symbol = _display_symbol(best_row)
    if _is_canonical_symbol(display_symbol):
        return int(best_row["instrument_id"]), int(best_row["snapshot_id"])
    if best_row["asset_type"] == "mutual_fund":
        return int(best_row["instrument_id"]), int(best_row["snapshot_id"])
    return None


def repair_position_symbols_by_name() -> dict:
    """Move snapshots with synthetic name-symbols to known ticker instruments."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT ps.snapshot_id, ps.currency, ps.raw_line, "
            "       inst.instrument_id, inst.symbol AS old_symbol, inst.asset_type, inst.name "
            "  FROM position_snapshots ps "
            "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id "
            " WHERE inst.asset_type IN ('equity','etf','mutual_fund','bond') "
            "   AND (inst.symbol GLOB '*_*' OR inst.symbol IN ('DIVIDEND','BOUGHT','SOLD')) "
            " ORDER BY ps.as_of_date, ps.snapshot_id"
        ).fetchall()
        for row in rows:
            target_id = _resolved_instrument_id(
                conn,
                " ".join(x for x in [row["name"], row["raw_line"]] if x),
                row["currency"] or "USD",
                row["name"],
            )
            if target_id is None or target_id == row["instrument_id"]:
                skipped += 1
                continue
            try:
                conn.execute(
                    "UPDATE position_snapshots SET instrument_id = ? WHERE snapshot_id = ?",
                    (target_id, row["snapshot_id"]),
                )
            except sqlite3.IntegrityError:
                skipped += 1
                continue
            repaired += 1
            if len(examples) < 10:
                new_symbol = conn.execute(
                    "SELECT symbol FROM instruments WHERE instrument_id = ?", (target_id,)
                ).fetchone()["symbol"]
                examples.append({
                    "snapshot_id": row["snapshot_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": new_symbol,
                })
        conn.commit()
    return {"repaired": repaired, "skipped": skipped, "examples": examples}


def repair_transaction_symbols_from_holdings() -> dict:
    """Resolve synthetic transaction symbols directly or via same-statement holdings."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT t.transaction_id, t.account_id, t.statement_id, t.trade_date, "
            "       t.txn_type, t.quantity, t.currency, t.description, "
            "       inst.instrument_id, inst.symbol AS old_symbol, inst.option_root "
            "  FROM transactions t "
            "  LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            " WHERE 1=1 "
            "   AND t.txn_type NOT IN ('tax_withholding') "
            "   AND t.txn_type NOT LIKE 'option_%' "
            "   AND (inst.instrument_id IS NULL "
            "        OR (inst.asset_type <> 'option' "
            "            AND (inst.symbol GLOB '*_*' OR inst.symbol IN ('DIVIDEND','BOUGHT','SOLD')))) "
            " ORDER BY t.trade_date, t.transaction_id"
        ).fetchall()
        for row in rows:
            target_id = _resolved_instrument_id(
                conn,
                row["description"] or "",
                row["currency"] or "USD",
                strip_leading_verbs(row["description"] or "")[:120],
            )
            snapshot_id = None
            if target_id is None and row["statement_id"] is not None:
                match = _best_snapshot_match(conn, row)
                if match is not None:
                    target_id, snapshot_id = match
            if target_id is None or target_id == row["instrument_id"]:
                skipped += 1
                continue
            conn.execute(
                "UPDATE transactions SET instrument_id = ? WHERE transaction_id = ?",
                (target_id, row["transaction_id"]),
            )
            repaired += 1
            if len(examples) < 10:
                new_symbol = conn.execute(
                    "SELECT symbol FROM instruments WHERE instrument_id = ?", (target_id,)
                ).fetchone()["symbol"]
                examples.append({
                    "transaction_id": row["transaction_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": new_symbol,
                    "snapshot_id": snapshot_id,
                })
        conn.commit()
    return {"repaired": repaired, "skipped": skipped, "examples": examples}


def repair_option_transaction_instruments() -> dict:
    """Ensure option transactions keep option contract instruments, not equity fallbacks."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT t.transaction_id, t.currency, t.description, "
            "       inst.symbol AS old_symbol, inst.asset_type, inst.option_root "
            "  FROM transactions t "
            "  LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            " WHERE t.txn_type LIKE 'option_%' "
            "   AND (inst.instrument_id IS NULL OR inst.asset_type <> 'option' OR inst.option_root IS NULL) "
            " ORDER BY t.trade_date, t.transaction_id"
        ).fetchall()
        for row in rows:
            parsed = _parse_option_desc(row["description"] or "")
            if not parsed:
                skipped += 1
                continue
            root, option_type, expiry, strike = parsed
            if not expiry or strike is None:
                skipped += 1
                continue
            target_id = sqlite_db.upsert_instrument(
                conn,
                asset_type="option",
                symbol=root,
                currency=row["currency"] or "USD",
                name=(row["description"] or "")[:120],
                option_root=root,
                option_expiry=expiry,
                option_strike=strike,
                option_type=option_type,
            )
            conn.execute(
                "UPDATE transactions SET instrument_id = ? WHERE transaction_id = ?",
                (target_id, row["transaction_id"]),
            )
            repaired += 1
            if len(examples) < 10:
                examples.append({
                    "transaction_id": row["transaction_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": root,
                })
        conn.commit()
    return {"repaired": repaired, "skipped": skipped, "examples": examples}


def repair_tax_withholding_symbols() -> dict:
    """Attach tax-withholding rows to the nearest same-day dividend instrument."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT t.transaction_id, t.account_id, t.statement_id, t.trade_date, t.currency, "
            "       inst.symbol AS old_symbol, inst.instrument_id "
            "  FROM transactions t "
            "  LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            " WHERE t.txn_type = 'tax_withholding' "
            "   AND (inst.instrument_id IS NULL OR inst.symbol GLOB '*_*' OR inst.symbol IN ('DIVIDEND','BOUGHT','SOLD')) "
            " ORDER BY t.trade_date, t.transaction_id"
        ).fetchall()
        for row in rows:
            target = conn.execute(
                "SELECT t2.transaction_id, t2.instrument_id, inst.symbol "
                "  FROM transactions t2 "
                "  JOIN instruments inst ON inst.instrument_id = t2.instrument_id "
                " WHERE t2.account_id = ? AND t2.statement_id = ? "
                "   AND t2.trade_date = ? AND t2.currency = ? "
                "   AND t2.txn_type IN ('dividend','distribution','return_of_capital') "
                "   AND t2.instrument_id IS NOT NULL "
                "   AND inst.symbol NOT GLOB '*_*' "
                "   AND inst.symbol NOT IN ('DIVIDEND','BOUGHT','SOLD') "
                " ORDER BY ABS(t2.transaction_id - ?) LIMIT 1",
                (row["account_id"], row["statement_id"], row["trade_date"],
                 row["currency"], row["transaction_id"]),
            ).fetchone()
            if target is None:
                skipped += 1
                continue
            conn.execute(
                "UPDATE transactions SET instrument_id = ? WHERE transaction_id = ?",
                (target["instrument_id"], row["transaction_id"]),
            )
            repaired += 1
            if len(examples) < 10:
                examples.append({
                    "transaction_id": row["transaction_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": target["symbol"],
                })
        conn.commit()
    return {"repaired": repaired, "skipped": skipped, "examples": examples}


def repair_leading_verb_symbols() -> dict:
    """Update transactions whose current instrument symbol is BOUGHT/SOLD."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT t.transaction_id, t.description, t.currency, inst.symbol AS old_symbol "
            "  FROM transactions t "
            "  JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            " WHERE inst.symbol IN ('BOUGHT', 'SOLD') "
            " ORDER BY t.trade_date, t.transaction_id"
        ).fetchall()
        for r in rows:
            desc = r["description"] or ""
            resolved = resolve_ticker(strip_leading_verbs(desc))
            if not resolved:
                skipped += 1
                continue
            symbol, asset_type = resolved
            inst_id = _instrument_id(
                conn,
                symbol,
                asset_type,
                r["currency"] or "USD",
                strip_leading_verbs(desc).split("|")[0].strip() or None,
            )
            conn.execute(
                "UPDATE transactions SET instrument_id = ? WHERE transaction_id = ?",
                (inst_id, r["transaction_id"]),
            )
            repaired += 1
            if len(examples) < 10:
                examples.append({
                    "transaction_id": r["transaction_id"],
                    "old_symbol": r["old_symbol"],
                    "new_symbol": symbol,
                })
        conn.commit()
    return {"repaired": repaired, "skipped": skipped, "examples": examples}


def repair_option_roots() -> dict:
    """Backfill option_root/type/expiry/strike from option-expiration descriptions."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT inst.instrument_id, inst.symbol AS old_symbol, "
            "       MIN(t.description) AS sample "
            "  FROM instruments inst "
            "  JOIN transactions t ON t.instrument_id = inst.instrument_id "
            " WHERE (inst.asset_type = 'option' OR inst.symbol LIKE 'CALL_%' OR inst.symbol LIKE 'PUT_%') "
            "   AND (inst.option_root IS NULL OR inst.option_root = '' "
            "        OR inst.option_type IS NULL OR inst.option_expiry IS NULL OR inst.option_strike IS NULL) "
            " GROUP BY inst.instrument_id, inst.symbol "
            " ORDER BY inst.symbol"
        ).fetchall()
        for r in rows:
            parsed = _parse_option_desc(r["sample"] or "") or _parse_option_symbol(r["old_symbol"] or "")
            if not parsed:
                skipped += 1
                continue
            root, opt_type, expiry, strike = parsed
            try:
                conn.execute(
                    "UPDATE instruments "
                    "   SET asset_type = 'option', option_root = ?, option_type = ?, "
                    "       option_expiry = COALESCE(?, option_expiry), "
                    "       option_strike = COALESCE(?, option_strike) "
                    " WHERE instrument_id = ?",
                    (root, opt_type, expiry, strike, r["instrument_id"]),
                )
            except sqlite3.IntegrityError:
                conn.execute(
                    "UPDATE instruments "
                    "   SET asset_type = 'option', option_root = ?, option_type = ? "
                    " WHERE instrument_id = ?",
                    (root, opt_type, r["instrument_id"]),
                )
            repaired += 1
            if len(examples) < 10:
                examples.append({
                    "instrument_id": r["instrument_id"],
                    "old_symbol": r["old_symbol"],
                    "new_root": root,
                })
        conn.commit()
    return {"repaired": repaired, "skipped": skipped, "examples": examples}


def repair_symbols() -> dict:
    """Run all symbol-repair passes."""
    leading = repair_leading_verb_symbols()
    options = repair_option_roots()
    option_transactions = repair_option_transaction_instruments()
    positions = repair_position_symbols_by_name()
    transactions = repair_transaction_symbols_from_holdings()
    taxes = repair_tax_withholding_symbols()
    return {
        "leading_verbs": leading,
        "options": options,
        "option_transactions": option_transactions,
        "positions": positions,
        "transactions": transactions,
        "tax_withholding": taxes,
    }

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
from .fund_lookup import ensure_lookup_table, lookup_fund_instrument_id, lookup_status_summary

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
    "BOUGHT", "SOLD", "DIVIDEND", "DISTRIBUTION", "DISTRIB", "DISTRIB.", "TRANSFER",
    "NON_RES_TAX_WITHHELD", "NON-RES_TAX_WITHHELD", "UNKNOWN",
}
_BAD_SYMBOL_SQL = "(" + ",".join(repr(symbol) for symbol in sorted(_BAD_SYMBOLS)) + ")"
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
        fund_id = lookup_fund_instrument_id(
            conn,
            fund_name=best_row["name"] or best_row["raw_line"] or transaction["description"] or "",
            currency=currency,
            sample_description=" ".join(
                x for x in [transaction["description"], best_row["name"], best_row["raw_line"]] if x
            ),
        )
        if fund_id is not None:
            return fund_id, int(best_row["snapshot_id"])
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
            f"   AND (inst.symbol GLOB '*_*' OR inst.symbol IN {_BAD_SYMBOL_SQL}) "
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
            f"            AND (inst.symbol GLOB '*_*' OR inst.symbol IN {_BAD_SYMBOL_SQL}))) "
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


def repair_transaction_symbols_from_direct_names() -> dict:
    """Fix canonical-but-wrong instruments when the description directly resolves."""
    repaired = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT t.transaction_id, t.txn_type, t.currency, t.description, "
            "       inst.instrument_id, inst.symbol AS old_symbol, inst.asset_type "
            "  FROM transactions t "
            "  JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            " WHERE t.txn_type IN ('buy','sell','dividend','distribution','return_of_capital') "
            "   AND inst.asset_type <> 'option' "
            "   AND t.description IS NOT NULL "
            " ORDER BY t.trade_date, t.transaction_id"
        ).fetchall()
        for row in rows:
            target_id = _resolved_instrument_id(
                conn,
                row["description"] or "",
                row["currency"] or "USD",
                strip_leading_verbs(row["description"] or "")[:120],
            )
            if target_id is None or target_id == row["instrument_id"]:
                skipped += 1
                continue
            new_row = conn.execute(
                "SELECT symbol, asset_type FROM instruments WHERE instrument_id = ?", (target_id,)
            ).fetchone()
            if new_row is None or not _is_canonical_symbol(new_row["symbol"]):
                skipped += 1
                continue
            conn.execute(
                "UPDATE transactions SET instrument_id = ? WHERE transaction_id = ?",
                (target_id, row["transaction_id"]),
            )
            repaired += 1
            if len(examples) < 10:
                examples.append({
                    "transaction_id": row["transaction_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": new_row["symbol"],
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
            f"   AND (inst.instrument_id IS NULL OR inst.symbol GLOB '*_*' OR inst.symbol IN {_BAD_SYMBOL_SQL}) "
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
                f"   AND inst.symbol NOT IN {_BAD_SYMBOL_SQL} "
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


def repair_mutual_fund_lookup_symbols() -> dict:
    """Use reviewed fund-code lookups, and queue unresolved fund names."""
    snapshot_repaired = 0
    transaction_repaired = 0
    pending_before = pending_after = 0
    skipped = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        ensure_lookup_table(conn)
        conn.execute(
            "DELETE FROM instrument_identifier_lookups "
            " WHERE status = 'pending' "
            "   AND resolved_symbol IS NULL "
            "   AND (normalized_name LIKE '%REINVESTED%' "
            "        OR normalized_name LIKE '%EINVESTED%' "
            "        OR normalized_name LIKE '%ACCOUNT%' "
            "        OR normalized_name LIKE '%INVESTOR%' "
            "        OR normalized_name LIKE '%PAGE%' "
            "        OR normalized_name LIKE '%PREVIOUS STATEMENT%')"
        )
        conn.execute(
            "DELETE FROM instrument_identifier_lookups AS generic "
            " WHERE generic.status = 'pending' "
            "   AND generic.resolved_symbol IS NULL "
            "   AND generic.institution_code = '' "
            "   AND generic.normalized_name LIKE 'CIBC%FUND%' "
            "   AND EXISTS ("
            "       SELECT 1 FROM instrument_identifier_lookups AS specific "
            "        WHERE specific.identifier_type = generic.identifier_type "
            "          AND specific.asset_type = generic.asset_type "
            "          AND specific.normalized_name = generic.normalized_name "
            "          AND specific.currency = generic.currency "
            "          AND specific.institution_code <> ''"
            "   )"
        )
        conn.execute(
            "DELETE FROM instrument_identifier_lookups "
            " WHERE status = 'pending' "
            "   AND resolved_symbol IS NULL "
            "   AND institution_code = '' "
            "   AND (sample_description LIKE '% ETF%' "
            "        OR normalized_name LIKE 'INT FR %' "
            "        OR normalized_name LIKE '%INVESCO DB%')"
        )
        conn.execute(
            "DELETE FROM instrument_identifier_lookups "
            " WHERE status = 'pending' "
            "   AND resolved_symbol IS NULL "
            "   AND normalized_name = 'RBB FUND'"
        )
        pending_before = lookup_status_summary(conn).get("pending", 0)

        snapshots = conn.execute(
            "SELECT ps.snapshot_id, ps.currency, ps.raw_line, "
            "       inst.instrument_id, inst.symbol AS old_symbol, inst.name, "
            "       i.code AS institution_code "
            "  FROM position_snapshots ps "
            "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id "
            "  JOIN statements s ON s.statement_id = ps.statement_id "
            "  JOIN accounts a ON a.account_id = s.account_id "
            "  JOIN institutions i ON i.institution_id = a.institution_id "
            " WHERE inst.asset_type = 'mutual_fund' "
            "   AND (inst.symbol GLOB '*_*' OR inst.name LIKE '%FUND%' OR ps.raw_line LIKE '%FUND%') "
            " ORDER BY ps.as_of_date, ps.snapshot_id"
        ).fetchall()
        for row in snapshots:
            target_id = lookup_fund_instrument_id(
                conn,
                fund_name=row["name"] or row["raw_line"] or row["old_symbol"] or "",
                currency=row["currency"] or "CAD",
                institution_code=row["institution_code"],
                sample_description=" ".join(x for x in [row["name"], row["raw_line"]] if x),
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
            snapshot_repaired += 1
            if len(examples) < 10:
                new_symbol = conn.execute(
                    "SELECT symbol FROM instruments WHERE instrument_id = ?", (target_id,)
                ).fetchone()["symbol"]
                examples.append({
                    "kind": "snapshot",
                    "id": row["snapshot_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": new_symbol,
                })

        transactions = conn.execute(
            "SELECT t.transaction_id, t.currency, t.description, "
            "       inst.instrument_id, inst.symbol AS old_symbol, inst.name, "
            "       i.code AS institution_code "
            "  FROM transactions t "
            "  JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            "  JOIN accounts a ON a.account_id = t.account_id "
            "  JOIN institutions i ON i.institution_id = a.institution_id "
            " WHERE inst.asset_type = 'mutual_fund' "
            "   AND (inst.symbol GLOB '*_*' OR inst.name LIKE '%FUND%' OR t.description LIKE '%FUND%') "
            " ORDER BY t.trade_date, t.transaction_id"
        ).fetchall()
        for row in transactions:
            target_id = lookup_fund_instrument_id(
                conn,
                fund_name=row["name"] or row["description"] or row["old_symbol"] or "",
                currency=row["currency"] or "CAD",
                institution_code=row["institution_code"],
                sample_description=row["description"],
            )
            if target_id is None or target_id == row["instrument_id"]:
                skipped += 1
                continue
            conn.execute(
                "UPDATE transactions SET instrument_id = ? WHERE transaction_id = ?",
                (target_id, row["transaction_id"]),
            )
            transaction_repaired += 1
            if len(examples) < 10:
                new_symbol = conn.execute(
                    "SELECT symbol FROM instruments WHERE instrument_id = ?", (target_id,)
                ).fetchone()["symbol"]
                examples.append({
                    "kind": "transaction",
                    "id": row["transaction_id"],
                    "old_symbol": row["old_symbol"],
                    "new_symbol": new_symbol,
                })

        pending_after = lookup_status_summary(conn).get("pending", 0)
        conn.commit()
    return {
        "snapshot_repaired": snapshot_repaired,
        "transaction_repaired": transaction_repaired,
        "skipped": skipped,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "examples": examples,
    }


def repair_transfer_directions() -> dict:
    """Fix transfer rows whose sign/text clearly states direction."""
    repaired = 0
    examples: list[dict] = []
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT transaction_id, txn_type, quantity, net_amount, description "
            "  FROM transactions "
            " WHERE txn_type IN ('transfer_in', 'transfer_out') "
            " ORDER BY trade_date, transaction_id"
        ).fetchall()
        for row in rows:
            desc = (row["description"] or "").upper()
            quantity_out = (row["quantity"] or 0) < 0
            amount_out = (row["net_amount"] or 0) < 0
            text_out = "TRANSFER TO" in desc
            quantity_text_out = (
                re.search(r"(?:^|\s)-\d[\d,]*(?:\.\d+)?\s+(?:—|-|\|)", desc) is not None
                or re.search(r"(?:^|\s)\d[\d,]*(?:\.\d+)?-\s*(?:\||$)", desc) is not None
            )
            text_in = "TRANSFER FROM" in desc
            amount_in = (row["net_amount"] or 0) > 0
            quantity_in = (row["quantity"] or 0) > 0

            desired_type = None
            if quantity_out or amount_out or text_out or quantity_text_out:
                desired_type = "transfer_out"
            elif text_in or amount_in or quantity_in:
                desired_type = "transfer_in"

            if desired_type is None or desired_type == row["txn_type"]:
                continue
            conn.execute(
                "UPDATE transactions SET txn_type = ? WHERE transaction_id = ?",
                (desired_type, row["transaction_id"]),
            )
            repaired += 1
            if len(examples) < 10:
                examples.append({
                    "transaction_id": row["transaction_id"],
                    "old_type": row["txn_type"],
                    "new_type": desired_type,
                })
        conn.commit()
    return {"repaired": repaired, "examples": examples}


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
    direct_names = repair_transaction_symbols_from_direct_names()
    taxes = repair_tax_withholding_symbols()
    fund_lookups = repair_mutual_fund_lookup_symbols()
    transfers = repair_transfer_directions()
    return {
        "leading_verbs": leading,
        "options": options,
        "option_transactions": option_transactions,
        "positions": positions,
        "transactions": transactions,
        "direct_names": direct_names,
        "tax_withholding": taxes,
        "fund_lookups": fund_lookups,
        "transfers": transfers,
    }

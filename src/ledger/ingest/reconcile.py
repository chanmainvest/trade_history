"""Transfer pairing, movement attribution, and checkpoint reconciliation."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..db import sqlite as sqlite_db
from ..quantity import (
    LEGACY_UNDERIVABLE_POSITION_TYPES,
    NON_CASH_TXN_TYPES,
    POSITION_AFFECTING_TYPES,
    contextual_position_delta,
    normalized_position_delta,
    quantity_delta,
)
from ..statement_selection import canonical_statement_clause

TRANSFER_TYPES = {"transfer_in", "transfer_out", "journal"}
AUTO_TRANSFER_NOTE_RE = re.compile(r"auto: matched transfer transactions (\d+) <-> (\d+)")
RECONCILIATION_KEY_PREFIX = "recon:v1:"
EXACT_TOLERANCE = 1e-9
POSITION_TOLERANCE = 1e-8
CASH_TOLERANCE = 0.01
POSITION_EFFECT_TYPES = POSITION_AFFECTING_TYPES
AUTOMATIC_NAME_METHODS = {"account_holding_name", "portfolio_holding_name"}

_TRADE_VERB_RE = re.compile(r"^\s*(?:BOUGHT|SOLD|BUY|SELL)\s+", re.IGNORECASE)
_TRADE_NUMERIC_TAIL_RE = re.compile(
    r"(?<!\S)[+-]?\$?\d[\d,]*(?:\.\d+)?-?\s+"
    r"\$?[+-]?\d[\d,]*(?:\.\d+)?\s+"
    r"\$?[+-]?\d[\d,]*(?:\.\d+)?"
)


def _statement_periods_are_adjacent(
    prior: sqlite3.Row,
    current: sqlite3.Row,
) -> bool:
    """Return whether two statement scopes cover consecutive calendar days."""
    try:
        prior_end = date.fromisoformat(str(prior["period_end"]))
        current_start = date.fromisoformat(str(current["period_start"]))
    except (KeyError, TypeError, ValueError):
        return False
    return current_start == prior_end + timedelta(days=1)
_BROKER_REFERENCE_RE = re.compile(r"\b[A-Z]{2}-\d{6}\b")
_NAME_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_NAME_STOP_WORDS = {
    "A", "AN", "THE", "AND", "OF", "ON", "INC", "LTD", "PLC",
    "CORP", "CORPORATION", "COMPANY", "CO", "COM", "COMMON", "STOCK",
    "SHARE", "SHARES", "ETF", "UNIT", "UNITS", "TRUST", "FUND", "FUNDS",
    "FD", "FDS", "CLASS", "CL", "SPONSORED", "ADR", "UNSOLICITED",
    "LIMITED", "NEW", "SER", "SERIES", "SEG", "AS",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
}
_NAME_ABBREVIATIONS = {
    "RTY": "REALTY",
    "PPTYS": "PROPERTIES",
    "SVGS": "SAVINGS",
    "INT": "INTEREST",
    "MNG": "MINING",
    "INVT": "INVESTMENT",
    "INVTS": "INVESTMENT",
    "GRP": "GROUP",
    "ENER": "ENERGY",
    "RES": "RESOURCES",
    "BND": "BOND",
    "INDX": "INDEX",
    "TREAS": "TREASURY",
    "YR": "YEAR",
    "MKT": "MARKET",
    "GLB": "GLOBAL",
    "HORZN": "HORIZONS",
}
_GENERIC_NAME_TOKENS = {
    "BMO", "CIBC", "DIREXION", "GLOBAL", "HORIZONS", "INVESCO",
    "ISHARES", "PURPOSE", "RBB", "RBC", "SPROTT", "TD", "VANECK",
}


@dataclass(frozen=True)
class _NameObservation:
    instrument_id: int
    evidence_id: int | None
    as_of_date: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class _NameMatch:
    instrument_id: int
    evidence_id: int | None
    score: float
    words: tuple[str, ...]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _security_name_prefix(value: str | None) -> str:
    """Keep the printed security-name portion before trade/holding numerics."""
    text = (value or "").upper().replace("|", " ")
    text = _TRADE_VERB_RE.sub("", text)
    numeric_tail = _TRADE_NUMERIC_TAIL_RE.search(text)
    if numeric_tail:
        text = text[: numeric_tail.start()]
    text = _BROKER_REFERENCE_RE.sub(" ", text)
    return " ".join(text.split())


def _security_name_words(value: str | None) -> tuple[str, ...]:
    words: list[str] = []
    for raw_token in _NAME_TOKEN_RE.findall(_security_name_prefix(value)):
        token = _NAME_ABBREVIATIONS.get(raw_token, raw_token)
        # Small numbers can identify products such as a 20-year or 6-month
        # fund. Large standalone numbers in a name are usually leaked quantity.
        if token.isdigit() and len(token) > 2:
            continue
        if token in _NAME_STOP_WORDS or len(token) <= 1:
            continue
        if token not in words:
            words.append(token)
    return tuple(words)


def _name_similarity(query: tuple[str, ...], candidate: tuple[str, ...]) -> float:
    query_set = set(query)
    candidate_set = set(candidate)
    if not query_set or not candidate_set:
        return 0.0
    if query_set == candidate_set:
        return 1.0

    overlap = query_set & candidate_set
    if len(overlap) >= 2 and (overlap == query_set or overlap == candidate_set):
        return 0.97
    if len(overlap) >= 2:
        jaccard = len(overlap) / len(query_set | candidate_set)
        containment = len(overlap) / min(len(query_set), len(candidate_set))
        if jaccard >= 0.55 and containment >= 0.75:
            return 0.90
    if (
        query[0] == candidate[0]
        and query[0] not in _GENERIC_NAME_TOKENS
        and len(query[0]) >= 5
    ):
        # Broker abbreviations can leave only the distinctive issuer word in
        # common (for example BARRICK GOLD vs BARRICK MINING). This is allowed
        # only for a unique same-account candidate by _choose_name_match().
        return 0.86
    return 0.0


def _observation_distance(observation: _NameObservation, trade_date: str) -> int:
    observed = _parse_date(observation.as_of_date)
    traded = _parse_date(trade_date)
    if observed is None or traded is None:
        return 10**9
    return abs((observed - traded).days)


def _choose_name_match(
    query: tuple[str, ...],
    observations: dict[int, list[_NameObservation]],
    *,
    trade_date: str,
    portfolio_wide: bool,
) -> tuple[_NameMatch | None, bool]:
    """Return one defensible candidate and whether any plausible match existed."""
    matches: list[_NameMatch] = []
    for instrument_id, instrument_observations in observations.items():
        scored = [
            (_name_similarity(query, observation.words), observation)
            for observation in instrument_observations
        ]
        scored = [item for item in scored if item[0] > 0]
        if not scored:
            continue
        score, evidence = min(
            scored,
            key=lambda item: (-item[0], _observation_distance(item[1], trade_date)),
        )
        matches.append(
            _NameMatch(
                instrument_id=instrument_id,
                evidence_id=evidence.evidence_id,
                score=score,
                words=evidence.words,
            )
        )

    matches.sort(key=lambda item: (-item.score, item.instrument_id))
    if not matches:
        return None, False
    best = matches[0]
    if portfolio_wide:
        distinctive_overlap = (
            set(query) & set(best.words)
        ) - _GENERIC_NAME_TOKENS
        exact_distinctive_single = (
            set(query) == set(best.words)
            and len(distinctive_overlap) == 1
        )
        if best.score < 0.97 or (
            len(distinctive_overlap) < 2 and not exact_distinctive_single
        ):
            return None, True
    elif best.score < 0.85:
        return None, True
    if len(matches) > 1 and best.score - matches[1].score < 0.10:
        return None, True
    return best, True


def _add_name_observation(
    catalog: dict[tuple[int, str], dict[int, list[_NameObservation]]],
    portfolio_catalog: dict[str, dict[int, list[_NameObservation]]],
    row: sqlite3.Row,
    value: str | None,
) -> None:
    words = _security_name_words(value)
    if not words:
        return
    observation = _NameObservation(
        instrument_id=int(row["instrument_id"]),
        evidence_id=(int(row["evidence_id"]) if row["evidence_id"] is not None else None),
        as_of_date=str(row["as_of_date"]),
        words=words,
    )
    account_key = (int(row["account_id"]), str(row["currency"]))
    account_observations = catalog.setdefault(account_key, {}).setdefault(
        observation.instrument_id, []
    )
    if observation not in account_observations:
        account_observations.append(observation)
    portfolio_observations = portfolio_catalog.setdefault(
        str(row["currency"]), {}
    ).setdefault(observation.instrument_id, [])
    if observation not in portfolio_observations:
        portfolio_observations.append(observation)


def resolve_trade_instruments_from_holdings(
    path: Path | str | None = None,
) -> dict[str, int]:
    """Resolve name-only buys/sells from observed, canonical position names.

    This is a rebuildable reconciliation pass, not a public-symbol guesser.
    It considers only equity/ETF identities already printed in this ledger,
    requires native-currency agreement, prefers the same account, and leaves
    every ambiguous or generic family name unresolved.
    """
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        method_placeholders = ",".join("?" for _ in AUTOMATIC_NAME_METHODS)
        reset = int(
            conn.execute(
                f"""
                UPDATE transactions
                   SET instrument_id = NULL,
                       resolution_method = 'unresolved_printed_identity',
                       resolution_confidence = 0.0,
                       resolution_evidence_id = evidence_id
                 WHERE txn_type IN ('buy', 'sell')
                   AND resolution_method IN ({method_placeholders})
                """,
                sorted(AUTOMATIC_NAME_METHODS),
            ).rowcount
            or 0
        )

        catalog: dict[tuple[int, str], dict[int, list[_NameObservation]]] = {}
        portfolio_catalog: dict[str, dict[int, list[_NameObservation]]] = {}
        position_rows = conn.execute(
            f"""
            SELECT ps.account_id, ps.currency, ps.instrument_id, ps.evidence_id,
                   ps.as_of_date, ps.raw_line, i.name
              FROM position_snapshots ps
              JOIN instruments i ON i.instrument_id = ps.instrument_id
             WHERE i.asset_type IN ('equity', 'etf')
               AND {canonical_statement_clause('ps.statement_id')}
             ORDER BY ps.account_id, ps.currency, ps.as_of_date, ps.snapshot_id
            """
        ).fetchall()
        for row in position_rows:
            _add_name_observation(catalog, portfolio_catalog, row, row["name"])
            _add_name_observation(catalog, portfolio_catalog, row, row["raw_line"])

        transactions = conn.execute(
            f"""
            SELECT transaction_id, account_id, trade_date, currency, description
              FROM transactions
             WHERE txn_type IN ('buy', 'sell')
               AND instrument_id IS NULL
               AND resolution_method = 'unresolved_printed_identity'
               AND {canonical_statement_clause('statement_id')}
             ORDER BY trade_date, transaction_id
            """
        ).fetchall()
        metrics: Counter[str] = Counter(reset=reset, scanned=len(transactions))
        for transaction in transactions:
            query = _security_name_words(transaction["description"])
            if not query:
                metrics["unmatched"] += 1
                continue
            account_key = (int(transaction["account_id"]), str(transaction["currency"]))
            match, plausible = _choose_name_match(
                query,
                catalog.get(account_key, {}),
                trade_date=str(transaction["trade_date"]),
                portfolio_wide=False,
            )
            method = "account_holding_name"
            if match is None and not plausible:
                match, plausible = _choose_name_match(
                    query,
                    portfolio_catalog.get(str(transaction["currency"]), {}),
                    trade_date=str(transaction["trade_date"]),
                    portfolio_wide=True,
                )
                method = "portfolio_holding_name"
            if match is None:
                metrics["ambiguous" if plausible else "unmatched"] += 1
                continue
            conn.execute(
                """
                UPDATE transactions
                   SET instrument_id = ?, resolution_method = ?,
                       resolution_confidence = ?, resolution_evidence_id = ?
                 WHERE transaction_id = ?
                """,
                (
                    match.instrument_id,
                    method,
                    match.score,
                    match.evidence_id,
                    transaction["transaction_id"],
                ),
            )
            metrics[f"resolved_{method.removesuffix('_holding_name')}"] += 1
        metrics["resolved"] = (
            metrics["resolved_account"] + metrics["resolved_portfolio"]
        )
        metrics["candidate_positions"] = len(position_rows)
        return dict(metrics)


def _clear_auto_transfer_links(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT notes FROM account_links WHERE notes LIKE 'auto: matched transfer transactions %'"
    ).fetchall()
    transaction_ids: set[int] = set()
    for row in rows:
        match = AUTO_TRANSFER_NOTE_RE.search(row["notes"] or "")
        if match:
            transaction_ids.update({int(match.group(1)), int(match.group(2))})

    if transaction_ids:
        placeholders = ",".join("?" * len(transaction_ids))
        conn.execute(
            f"""
            UPDATE transactions
               SET counterpart_account_id = NULL,
                   counterpart_txn_id = NULL
             WHERE transaction_id IN ({placeholders})
                OR counterpart_txn_id IN ({placeholders})
            """,
            [*transaction_ids, *transaction_ids],
        )
    deleted = conn.execute(
        "DELETE FROM account_links WHERE notes LIKE 'auto: matched transfer transactions %'"
    ).rowcount
    return int(deleted or 0)


def _security_delta(row: sqlite3.Row) -> float:
    if row["instrument_id"] is None:
        return 0.0
    if "position_delta" in row.keys() and row["position_delta"] is not None:
        return float(row["position_delta"])
    if row["quantity"] is None:
        return 0.0
    return quantity_delta(row["txn_type"], row["quantity"])


def _position_effect(row: sqlite3.Row) -> float | None:
    """Return a position effect, or ``None`` for an unknown required effect."""
    if row["position_delta"] is not None:
        effect = float(row["position_delta"])
        if (
            row["txn_type"] in LEGACY_UNDERIVABLE_POSITION_TYPES
            and abs(effect) <= EXACT_TOLERANCE
        ):
            return None
        return effect
    effect = normalized_position_delta(row["txn_type"], row["quantity"])
    if effect is None:
        return None if row["txn_type"] in POSITION_EFFECT_TYPES else 0.0
    return effect


def _cash_delta(row: sqlite3.Row) -> float:
    if row["instrument_id"] is not None:
        return 0.0
    amount_value = (
        row["cash_delta"]
        if "cash_delta" in row.keys() and row["cash_delta"] is not None
        else row["net_amount"]
    )
    if amount_value is None:
        return 0.0
    amount = float(amount_value)
    if row["txn_type"] == "transfer_in":
        return abs(amount)
    if row["txn_type"] == "transfer_out":
        return -abs(amount)
    return amount


def _transfer_key(row: sqlite3.Row) -> tuple[str, str, str, float] | None:
    security_delta = _security_delta(row)
    if abs(security_delta) > 1e-9 and row["instrument_key"] is not None:
        return (
            "instrument",
            str(row["instrument_key"]),
            row["currency"] or "",
            round(abs(security_delta), 8),
        )
    cash_delta = _cash_delta(row)
    if abs(cash_delta) > 0.005:
        return ("cash", row["currency"] or "", row["currency"] or "", round(abs(cash_delta), 2))
    return None


def _transfer_delta(row: sqlite3.Row) -> float:
    security_delta = _security_delta(row)
    if abs(security_delta) > 1e-9:
        return security_delta
    return _cash_delta(row)


def _journal_pair_ratios(
    conn: sqlite3.Connection,
) -> dict[tuple[int, int], tuple[float, str | None, str | None]]:
    return {
        (int(row["from_instrument_id"]), int(row["to_instrument_id"])): (
            float(row["conversion_ratio"]),
            row["effective_from"],
            row["effective_to"],
        )
        for row in conn.execute(
            """
            SELECT from_instrument_id, to_instrument_id, conversion_ratio,
                   effective_from, effective_to
              FROM instrument_journal_pairs
             WHERE status IN ('catalog','reviewed')
            """
        ).fetchall()
    }


def _journal_compatible(
    outgoing: sqlite3.Row,
    incoming: sqlite3.Row,
    pairs: dict[tuple[int, int], tuple[float, str | None, str | None]],
) -> bool:
    out_id, in_id = outgoing["instrument_id"], incoming["instrument_id"]
    if out_id is None or in_id is None or int(out_id) == int(in_id):
        return False
    pair = pairs.get((int(out_id), int(in_id)))
    reverse = False
    if pair is None:
        pair = pairs.get((int(in_id), int(out_id)))
        reverse = pair is not None
    if pair is None:
        return False
    ratio, effective_from, effective_to = pair
    event_date = max(str(outgoing["trade_date"]), str(incoming["trade_date"]))
    if effective_from is not None and event_date < effective_from:
        return False
    if effective_to is not None and event_date > effective_to:
        return False
    out_quantity = abs(_security_delta(outgoing))
    in_quantity = abs(_security_delta(incoming))
    expected = out_quantity / ratio if reverse else out_quantity * ratio
    return abs(in_quantity - expected) <= 1e-8


def link_transfers(
    path: Path | str | None = None,
    *,
    date_window_days: int = 7,
) -> dict:
    """Pair unambiguous transfer rows and populate counterpart fields."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        cleared = _clear_auto_transfer_links(conn)
        rows = conn.execute(
            f"""
            SELECT t.transaction_id, t.account_id, t.trade_date, t.txn_type,
                   t.instrument_id, i.instrument_key, t.quantity, t.position_delta,
                   t.net_amount, t.cash_delta, t.currency, t.description
              FROM transactions t
              LEFT JOIN instruments i ON i.instrument_id = t.instrument_id
             WHERE txn_type IN ('transfer_in', 'transfer_out', 'journal')
               AND counterpart_txn_id IS NULL
               AND {canonical_statement_clause("t.statement_id")}
             ORDER BY trade_date, transaction_id
            """
        ).fetchall()
        journal_pairs = _journal_pair_ratios(conn)

        incoming: dict[tuple[str, str, str, float], list[sqlite3.Row]] = {}
        incoming_security: list[sqlite3.Row] = []
        outgoing: list[sqlite3.Row] = []
        skipped_missing_key = 0
        for row in rows:
            key = _transfer_key(row)
            delta = _transfer_delta(row)
            if key is None or abs(delta) <= 1e-9:
                skipped_missing_key += 1
                continue
            if delta > 0:
                incoming.setdefault(key, []).append(row)
                if key[0] == "instrument":
                    incoming_security.append(row)
            else:
                outgoing.append(row)

        matched_incoming_ids: set[int] = set()
        matched = 0
        ambiguous = 0
        for out_row in outgoing:
            out_date = _parse_date(out_row["trade_date"])
            key = _transfer_key(out_row)
            if out_date is None or key is None:
                skipped_missing_key += 1
                continue
            candidate_rows = list(incoming.get(key, []))
            if key[0] == "instrument":
                candidate_rows.extend(
                    row
                    for row in incoming_security
                    if row not in candidate_rows
                    and _journal_compatible(out_row, row, journal_pairs)
                )
            candidates: list[tuple[int, sqlite3.Row]] = []
            for in_row in candidate_rows:
                in_id = int(in_row["transaction_id"])
                if in_id in matched_incoming_ids or in_row["account_id"] == out_row["account_id"]:
                    continue
                in_date = _parse_date(in_row["trade_date"])
                if in_date is None:
                    continue
                days_apart = abs((in_date - out_date).days)
                if days_apart <= date_window_days:
                    candidates.append((days_apart, in_row))
            if not candidates:
                continue
            candidates.sort(key=lambda item: (item[0], item[1]["trade_date"], item[1]["transaction_id"]))
            best_distance = candidates[0][0]
            best = [candidate for distance, candidate in candidates if distance == best_distance]
            if len(best) != 1:
                ambiguous += 1
                continue
            in_row = best[0]
            out_id = int(out_row["transaction_id"])
            in_id = int(in_row["transaction_id"])
            conn.execute(
                """
                UPDATE transactions
                   SET counterpart_account_id = ?, counterpart_txn_id = ?
                 WHERE transaction_id = ?
                """,
                (in_row["account_id"], in_id, out_id),
            )
            conn.execute(
                """
                UPDATE transactions
                   SET counterpart_account_id = ?, counterpart_txn_id = ?
                 WHERE transaction_id = ?
                """,
                (out_row["account_id"], out_id, in_id),
            )
            conn.execute(
                """
                INSERT INTO account_links(from_account_id, to_account_id, transfer_date, notes)
                VALUES (?, ?, ?, ?)
                """,
                (
                    out_row["account_id"],
                    in_row["account_id"],
                    min(out_row["trade_date"], in_row["trade_date"]),
                    f"auto: matched transfer transactions {out_id} <-> {in_id}",
                ),
            )
            matched_incoming_ids.add(in_id)
            matched += 1

    return {
        "matched": matched,
        "cleared": cleared,
        "ambiguous": ambiguous,
        "skipped_missing_key": skipped_missing_key,
    }


def rebuild_position_transaction_links(path: Path | str | None = None) -> dict:
    """Rebuild monthly snapshot movement attribution from transaction rows."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        conn.execute("DELETE FROM position_transaction_links")
        snapshots = conn.execute(
            f"""
            SELECT ps.snapshot_id, ps.account_id, ps.instrument_id, ps.as_of_date,
                   ps.statement_id, ps.currency, i.instrument_key
              FROM position_snapshots ps
              JOIN instruments i ON i.instrument_id = ps.instrument_id
              JOIN snapshot_sets ss ON ss.snapshot_set_id = ps.snapshot_set_id
             WHERE ss.section_type = 'positions'
               AND ss.completeness = 'complete'
               AND {canonical_statement_clause("ps.statement_id")}
             ORDER BY ps.account_id, i.instrument_key, ps.currency,
                      ps.as_of_date, ps.statement_id, ps.snapshot_id
            """
        ).fetchall()
        previous_snapshot_date: dict[tuple[int, str, str], str] = {}
        linked = 0
        for snapshot in snapshots:
            key = (
                int(snapshot["account_id"]),
                str(snapshot["instrument_key"]),
                str(snapshot["currency"]),
            )
            params: list = [snapshot["account_id"], snapshot["instrument_key"], snapshot["as_of_date"]]
            previous_date = previous_snapshot_date.get(key)
            previous_clause = ""
            if previous_date is not None:
                previous_clause = "AND trade_date > ?"
                params.append(previous_date)
            transactions = conn.execute(
                f"""
                SELECT t.transaction_id, t.instrument_id, t.txn_type, t.quantity,
                       t.position_delta
                  FROM transactions t
                  JOIN instruments i ON i.instrument_id = t.instrument_id
                 WHERE t.account_id = ?
                   AND i.instrument_key = ?
                   AND t.trade_date <= ?
                   AND {canonical_statement_clause("t.statement_id")}
                   {previous_clause}
                 ORDER BY trade_date, transaction_id
                """,
                params,
            ).fetchall()
            for transaction in transactions:
                attributed = _security_delta(transaction)
                if abs(attributed) <= 1e-9:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO position_transaction_links(
                        snapshot_id, transaction_id, quantity_attributed
                    ) VALUES (?, ?, ?)
                    """,
                    (snapshot["snapshot_id"], transaction["transaction_id"], attributed),
                )
                linked += 1
            previous_snapshot_date[key] = snapshot["as_of_date"]
    return {"links": linked, "snapshots": len(snapshots)}


def _result_status(residual: float, tolerance: float) -> str:
    absolute = abs(residual)
    if absolute <= EXACT_TOLERANCE:
        return "reconciled"
    if absolute <= tolerance:
        return "within_rounding"
    return "unexplained_residual"


def _residual_reason(residual: float, tolerance: float) -> str | None:
    """Describe only a residual which exceeds its documented tolerance."""
    if _result_status(residual, tolerance) != "unexplained_residual":
        return None
    return (
        f"residual {residual:.8g} exceeds documented tolerance {tolerance:.8g}"
    )


def _add_result_metric(metrics: Counter[str], status: str) -> None:
    metrics["results"] += 1
    metrics[status] += 1


def _write_reconciliation_result(
    conn: sqlite3.Connection,
    *,
    reconciliation_key: str,
    ingestion_run_id: int | None,
    kind: str,
    account_id: int,
    statement_id: int | None,
    snapshot_set_id: int | None,
    prior_snapshot_set_id: int | None,
    instrument_id: int | None,
    currency: str,
    prior_checkpoint: str | None,
    current_checkpoint: str | None,
    opening_value: float | None,
    summed_deltas: float | None,
    expected_close: float | None,
    reported_close: float | None,
    residual: float | None,
    tolerance: float,
    status: str,
    reason: str | None,
    components: list[tuple[int, float]] | None = None,
) -> int:
    reconciliation_id = int(
        conn.execute(
            """
            INSERT INTO reconciliation_results(
                reconciliation_key, ingestion_run_id, kind, account_id,
                statement_id, snapshot_set_id, prior_snapshot_set_id,
                instrument_id, currency, prior_checkpoint, current_checkpoint,
                opening_value, summed_deltas, expected_close, reported_close,
                residual, tolerance, status, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING reconciliation_id
            """,
            (
                reconciliation_key,
                ingestion_run_id,
                kind,
                account_id,
                statement_id,
                snapshot_set_id,
                prior_snapshot_set_id,
                instrument_id,
                currency,
                prior_checkpoint,
                current_checkpoint,
                opening_value,
                summed_deltas,
                expected_close,
                reported_close,
                residual,
                tolerance,
                status,
                reason,
            ),
        ).fetchone()[0]
    )
    if components:
        conn.executemany(
            """
            INSERT INTO reconciliation_components(reconciliation_id, transaction_id, delta)
            VALUES (?, ?, ?)
            """,
            [(reconciliation_id, transaction_id, delta) for transaction_id, delta in components],
        )
    return reconciliation_id


def _position_rows_by_key(
    conn: sqlite3.Connection,
    snapshot_set_id: int,
) -> dict[str, tuple[int, float]]:
    rows = conn.execute(
        """
        SELECT ps.instrument_id, i.instrument_key, ps.quantity
          FROM position_snapshots ps
          JOIN instruments i ON i.instrument_id = ps.instrument_id
         WHERE ps.snapshot_set_id = ?
        """,
        (snapshot_set_id,),
    ).fetchall()
    return {
        str(row["instrument_key"]): (int(row["instrument_id"]), float(row["quantity"]))
        for row in rows
    }


def _position_interval_replay(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    currency: str,
    prior_checkpoint: str,
    current_checkpoint: str,
    prior_rows: dict[str, tuple[int, float]],
) -> tuple[
    dict[str, float],
    dict[str, list[tuple[int, float]]],
    dict[str, int],
    dict[str, int],
]:
    """Replay one checkpoint interval, including whole-position ticker moves."""
    rows = conn.execute(
        f"""
        SELECT t.transaction_id, t.txn_type, t.quantity, t.position_delta,
               i.instrument_id, i.instrument_key,
               tc.conversion_ratio,
               successor.instrument_key AS successor_key,
               successor.instrument_id AS successor_id
          FROM transactions t
          LEFT JOIN instruments i ON i.instrument_id = t.instrument_id
          LEFT JOIN instrument_ticker_change_sources source
                 ON source.transaction_id = t.transaction_id
          LEFT JOIN instrument_ticker_changes tc
                 ON tc.ticker_change_id = source.ticker_change_id
          LEFT JOIN instruments successor
                 ON successor.instrument_id = tc.to_instrument_id
         WHERE t.account_id = ?
           AND t.currency = ?
           AND t.trade_date > ?
           AND t.trade_date <= ?
           AND {canonical_statement_clause("t.statement_id")}
         ORDER BY t.trade_date, t.transaction_id
        """,
        (account_id, currency, prior_checkpoint, current_checkpoint),
    ).fetchall()
    balances = {key: value for key, (_instrument_id, value) in prior_rows.items()}
    instrument_ids = {key: iid for key, (iid, _value) in prior_rows.items()}
    components: dict[str, list[tuple[int, float]]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    for row in rows:
        key = row["instrument_key"]
        if key is None:
            continue
        key = str(key)
        instrument_ids.setdefault(key, int(row["instrument_id"]))
        transaction_id = int(row["transaction_id"])
        if row["successor_key"] is not None:
            successor_key = str(row["successor_key"])
            moved = balances.get(key, 0.0)
            ratio = float(row["conversion_ratio"])
            old_delta = -moved
            new_delta = moved * ratio
            balances[key] = 0.0
            balances[successor_key] = balances.get(successor_key, 0.0) + new_delta
            instrument_ids[successor_key] = int(row["successor_id"])
            if abs(old_delta) > EXACT_TOLERANCE:
                components[key].append((transaction_id, old_delta))
            if abs(new_delta) > EXACT_TOLERANCE:
                components[successor_key].append((transaction_id, new_delta))
            continue
        effect = _position_effect(row)
        effect = contextual_position_delta(
            str(row["txn_type"]),
            row["quantity"],
            balances.get(key, 0.0),
            effect,
        )
        if effect is None:
            if row["txn_type"] in POSITION_EFFECT_TYPES:
                missing[key] += 1
            continue
        balances[key] = balances.get(key, 0.0) + effect
        if abs(effect) > EXACT_TOLERANCE:
            components[key].append((transaction_id, effect))
    return balances, components, missing, instrument_ids


def _unresolved_position_effect_count(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    currency: str,
    prior_checkpoint: str,
    current_checkpoint: str,
) -> int:
    placeholders = ",".join("?" * len(POSITION_EFFECT_TYPES))
    row = conn.execute(
        f"""
        SELECT COUNT(*)
          FROM transactions t
         WHERE t.account_id = ?
           AND t.currency = ?
           AND t.instrument_id IS NULL
           AND t.trade_date > ?
           AND t.trade_date <= ?
           AND t.txn_type IN ({placeholders})
           AND {canonical_statement_clause("t.statement_id")}
        """,
        (
            account_id,
            currency,
            prior_checkpoint,
            current_checkpoint,
            *sorted(POSITION_EFFECT_TYPES),
        ),
    ).fetchone()
    return int(row[0]) if row else 0


def _reconcile_position_scopes(conn: sqlite3.Connection) -> dict[str, int]:
    scopes = conn.execute(
        f"""
        SELECT ss.snapshot_set_id, ss.statement_id, ss.account_id, ss.as_of_date,
               ss.currency, ss.scope_key, ss.completeness, s.ingestion_run_id,
               s.period_start, s.period_end
          FROM snapshot_sets ss
          JOIN statements s ON s.statement_id = ss.statement_id
         WHERE ss.section_type = 'positions'
           AND {canonical_statement_clause("ss.statement_id")}
         ORDER BY ss.account_id, ss.currency, ss.scope_key, ss.as_of_date,
                  ss.statement_id, ss.snapshot_set_id
        """
    ).fetchall()
    previous: dict[tuple[int, str, str], sqlite3.Row] = {}
    metrics: Counter[str] = Counter()

    for scope in scopes:
        scope_key = (
            int(scope["account_id"]),
            str(scope["currency"]),
            str(scope["scope_key"]),
        )
        prior = previous.get(scope_key)
        current_rows = _position_rows_by_key(conn, int(scope["snapshot_set_id"]))
        prior_rows = (
            _position_rows_by_key(conn, int(prior["snapshot_set_id"]))
            if prior is not None
            else {}
        )
        interval_balances: dict[str, float] = {}
        interval_components: dict[str, list[tuple[int, float]]] = {}
        interval_missing: dict[str, int] = {}
        interval_ids: dict[str, int] = {}
        if prior is not None:
            (
                interval_balances,
                interval_components,
                interval_missing,
                interval_ids,
            ) = _position_interval_replay(
                conn,
                account_id=int(scope["account_id"]),
                currency=str(scope["currency"]),
                prior_checkpoint=str(prior["as_of_date"]),
                current_checkpoint=str(scope["as_of_date"]),
                prior_rows=prior_rows,
            )
        instrument_keys = sorted(
            set(prior_rows) | set(current_rows) | set(interval_balances)
        )

        if not instrument_keys:
            if scope["completeness"] != "complete":
                status = "incomplete_input"
                reason = "current position scope is not complete"
            elif prior is None:
                status = "missing_prior_checkpoint"
                reason = "no prior position scope"
            elif prior["completeness"] != "complete":
                status = "incomplete_input"
                reason = "prior position scope is not complete"
            elif not _statement_periods_are_adjacent(prior, scope):
                status = "incomplete_input"
                reason = "position checkpoint interval has an unobserved statement period"
            else:
                unresolved_effects = _unresolved_position_effect_count(
                    conn,
                    account_id=scope["account_id"],
                    currency=scope["currency"],
                    prior_checkpoint=prior["as_of_date"],
                    current_checkpoint=scope["as_of_date"],
                )
                if unresolved_effects:
                    status = "incomplete_input"
                    reason = (
                        f"{unresolved_effects} position-affecting transaction(s) "
                        "lack an instrument"
                    )
                else:
                    status = "not_applicable"
                    reason = (
                        "complete position scope has no positions in either checkpoint"
                    )
            _write_reconciliation_result(
                conn,
                reconciliation_key=f"{RECONCILIATION_KEY_PREFIX}position:{scope['snapshot_set_id']}:scope",
                ingestion_run_id=scope["ingestion_run_id"],
                kind="position",
                account_id=scope["account_id"],
                statement_id=scope["statement_id"],
                snapshot_set_id=scope["snapshot_set_id"],
                prior_snapshot_set_id=prior["snapshot_set_id"] if prior else None,
                instrument_id=None,
                currency=scope["currency"],
                prior_checkpoint=prior["as_of_date"] if prior else None,
                current_checkpoint=scope["as_of_date"],
                opening_value=None,
                summed_deltas=None,
                expected_close=None,
                reported_close=None,
                residual=None,
                tolerance=POSITION_TOLERANCE,
                status=status,
                reason=reason,
            )
            _add_result_metric(metrics, status)
            previous[scope_key] = scope
            continue

        unresolved_effects = 0
        if prior is not None:
            unresolved_effects = _unresolved_position_effect_count(
                conn,
                account_id=scope["account_id"],
                currency=scope["currency"],
                prior_checkpoint=prior["as_of_date"],
                current_checkpoint=scope["as_of_date"],
            )

        for instrument_key in instrument_keys:
            current_value = current_rows.get(instrument_key)
            prior_value = prior_rows.get(instrument_key)
            instrument_id = (
                current_value[0]
                if current_value is not None
                else prior_value[0]
                if prior_value is not None
                else interval_ids.get(instrument_key)
            )
            opening_value = prior_value[1] if prior_value is not None else None
            reported_close = current_value[1] if current_value is not None else 0.0
            components: list[tuple[int, float]] = []
            summed_deltas: float | None = None
            expected_close: float | None = None
            residual: float | None = None

            if scope["completeness"] != "complete":
                status = "incomplete_input"
                reason = "current position scope is not complete"
            elif prior is None:
                status = "missing_prior_checkpoint"
                reason = "no prior position scope"
            elif prior["completeness"] != "complete":
                status = "incomplete_input"
                reason = "prior position scope is not complete"
            elif not _statement_periods_are_adjacent(prior, scope):
                status = "incomplete_input"
                reason = "position checkpoint interval has an unobserved statement period"
            else:
                components = interval_components.get(instrument_key, [])
                missing_effects = interval_missing.get(instrument_key, 0)
                summed_deltas = sum(delta for _transaction_id, delta in components)
                if unresolved_effects or missing_effects:
                    status = "incomplete_input"
                    reason_parts = []
                    if unresolved_effects:
                        reason_parts.append(
                            f"{unresolved_effects} position-affecting transaction(s) lack an instrument"
                        )
                    if missing_effects:
                        reason_parts.append(
                            f"{missing_effects} transaction(s) lack a position delta"
                        )
                    reason = "; ".join(reason_parts)
                else:
                    opening_value = prior_value[1] if prior_value is not None else 0.0
                    expected_close = interval_balances.get(instrument_key, opening_value)
                    residual = reported_close - expected_close
                    status = _result_status(residual, POSITION_TOLERANCE)
                    reason = _residual_reason(residual, POSITION_TOLERANCE)

            _write_reconciliation_result(
                conn,
                reconciliation_key=(
                    f"{RECONCILIATION_KEY_PREFIX}position:"
                    f"{scope['snapshot_set_id']}:{instrument_id}"
                ),
                ingestion_run_id=scope["ingestion_run_id"],
                kind="position",
                account_id=scope["account_id"],
                statement_id=scope["statement_id"],
                snapshot_set_id=scope["snapshot_set_id"],
                prior_snapshot_set_id=prior["snapshot_set_id"] if prior else None,
                instrument_id=instrument_id,
                currency=scope["currency"],
                prior_checkpoint=prior["as_of_date"] if prior else None,
                current_checkpoint=scope["as_of_date"],
                opening_value=opening_value,
                summed_deltas=summed_deltas,
                expected_close=expected_close,
                reported_close=reported_close,
                residual=residual,
                tolerance=POSITION_TOLERANCE,
                status=status,
                reason=reason,
                components=components,
            )
            _add_result_metric(metrics, status)
        previous[scope_key] = scope
    return dict(metrics)


def _cash_balance_for_scope(
    conn: sqlite3.Connection,
    snapshot_set_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT cash_balance_id, opening_balance, closing_balance
          FROM cash_balances
         WHERE snapshot_set_id = ?
        """,
        (snapshot_set_id,),
    ).fetchone()


def _cash_components(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    currency: str,
    period_start: str,
    period_end: str,
) -> tuple[list[tuple[int, float]], int]:
    """Return cash movements that take effect in one statement's cash period.

    A trade may be reported in the prior statement while settling in this one,
    so statement ownership is not a safe cash interval.  ``cash_effective_date``
    is normalized during persistence from the broker-specific contract and
    falls back to the trade date for legacy rows.
    """
    rows = conn.execute(
        f"""
        SELECT t.transaction_id, t.txn_type, t.cash_delta, t.net_amount
          FROM transactions t
         WHERE t.account_id = ?
           AND t.currency = ?
           AND COALESCE(t.cash_effective_date, t.trade_date) >= ?
           AND COALESCE(t.cash_effective_date, t.trade_date) <= ?
           AND {canonical_statement_clause("t.statement_id")}
         ORDER BY COALESCE(t.cash_effective_date, t.trade_date), t.transaction_id
        """,
        (account_id, currency, period_start, period_end),
    ).fetchall()
    components: list[tuple[int, float]] = []
    missing_effects = 0
    for row in rows:
        if row["txn_type"] in NON_CASH_TXN_TYPES:
            continue
        value = row["cash_delta"] if row["cash_delta"] is not None else row["net_amount"]
        if value is None:
            missing_effects += 1
            continue
        components.append((int(row["transaction_id"]), float(value)))
    return components, missing_effects


def _reconcile_cash_scopes(conn: sqlite3.Connection) -> dict[str, int]:
    scopes = conn.execute(
        f"""
        SELECT ss.snapshot_set_id, ss.statement_id, ss.account_id, ss.as_of_date,
               ss.currency, ss.scope_key, ss.completeness, s.period_start,
               s.period_end, s.ingestion_run_id
          FROM snapshot_sets ss
          JOIN statements s ON s.statement_id = ss.statement_id
         WHERE ss.section_type = 'cash'
           AND {canonical_statement_clause("ss.statement_id")}
         ORDER BY ss.account_id, ss.currency, ss.scope_key, ss.as_of_date,
                  ss.statement_id, ss.snapshot_set_id
        """
    ).fetchall()
    previous: dict[tuple[int, str, str], sqlite3.Row] = {}
    metrics: Counter[str] = Counter()

    for scope in scopes:
        scope_key = (
            int(scope["account_id"]),
            str(scope["currency"]),
            str(scope["scope_key"]),
        )
        prior = previous.get(scope_key)
        balance = _cash_balance_for_scope(conn, int(scope["snapshot_set_id"]))
        components, missing_effects = _cash_components(
            conn,
            account_id=scope["account_id"],
            currency=scope["currency"],
            period_start=scope["period_start"],
            period_end=scope["period_end"],
        )
        opening_value = float(balance["opening_balance"]) if balance and balance["opening_balance"] is not None else None
        reported_close = float(balance["closing_balance"]) if balance else None
        summed_deltas = sum(delta for _transaction_id, delta in components)
        expected_close: float | None = None
        residual: float | None = None

        if scope["completeness"] != "complete":
            status = "incomplete_input"
            reason = "current cash scope is not complete"
        elif balance is None:
            status = "incomplete_input"
            reason = "cash scope has no closing balance row"
        elif opening_value is None:
            status = "incomplete_input"
            reason = "cash scope has no printed opening balance"
        elif missing_effects:
            status = "incomplete_input"
            reason = f"{missing_effects} transaction(s) have no cash delta"
        else:
            expected_close = opening_value + summed_deltas
            residual = reported_close - expected_close
            status = _result_status(residual, CASH_TOLERANCE)
            reason = _residual_reason(residual, CASH_TOLERANCE)

        _write_reconciliation_result(
            conn,
            reconciliation_key=f"{RECONCILIATION_KEY_PREFIX}cash:statement:{scope['snapshot_set_id']}",
            ingestion_run_id=scope["ingestion_run_id"],
            kind="cash",
            account_id=scope["account_id"],
            statement_id=scope["statement_id"],
            snapshot_set_id=scope["snapshot_set_id"],
            prior_snapshot_set_id=None,
            instrument_id=None,
            currency=scope["currency"],
            prior_checkpoint=None,
            current_checkpoint=scope["as_of_date"],
            opening_value=opening_value,
            summed_deltas=summed_deltas,
            expected_close=expected_close,
            reported_close=reported_close,
            residual=residual,
            tolerance=CASH_TOLERANCE,
            status=status,
            reason=reason,
            components=components,
        )
        _add_result_metric(metrics, status)

        prior_balance = (
            _cash_balance_for_scope(conn, int(prior["snapshot_set_id"]))
            if prior is not None
            else None
        )
        continuity_opening = (
            float(prior_balance["closing_balance"])
            if prior_balance is not None
            else None
        )
        continuity_reported = opening_value
        continuity_residual: float | None = None
        if scope["completeness"] != "complete":
            continuity_status = "incomplete_input"
            continuity_reason = "current cash scope is not complete"
        elif prior is None:
            continuity_status = "missing_prior_checkpoint"
            continuity_reason = "no prior cash scope"
        elif prior["completeness"] != "complete":
            continuity_status = "incomplete_input"
            continuity_reason = "prior cash scope is not complete"
        elif not _statement_periods_are_adjacent(prior, scope):
            continuity_status = "incomplete_input"
            continuity_reason = "cash continuity has an unobserved statement period"
        elif prior_balance is None or balance is None or opening_value is None:
            continuity_status = "incomplete_input"
            continuity_reason = "missing prior close or current opening balance"
        else:
            continuity_residual = continuity_reported - continuity_opening
            continuity_status = _result_status(continuity_residual, CASH_TOLERANCE)
            continuity_reason = _residual_reason(
                continuity_residual,
                CASH_TOLERANCE,
            )

        _write_reconciliation_result(
            conn,
            reconciliation_key=f"{RECONCILIATION_KEY_PREFIX}cash:continuity:{scope['snapshot_set_id']}",
            ingestion_run_id=scope["ingestion_run_id"],
            kind="cash",
            account_id=scope["account_id"],
            statement_id=scope["statement_id"],
            snapshot_set_id=scope["snapshot_set_id"],
            prior_snapshot_set_id=prior["snapshot_set_id"] if prior else None,
            instrument_id=None,
            currency=scope["currency"],
            prior_checkpoint=prior["as_of_date"] if prior else None,
            current_checkpoint=scope["as_of_date"],
            opening_value=continuity_opening,
            summed_deltas=0.0 if continuity_opening is not None else None,
            expected_close=continuity_opening,
            reported_close=continuity_reported,
            residual=continuity_residual,
            tolerance=CASH_TOLERANCE,
            status=continuity_status,
            reason=continuity_reason,
        )
        _add_result_metric(metrics, continuity_status)
        previous[scope_key] = scope
    return dict(metrics)


def _reconcile_statement_totals(conn: sqlite3.Connection) -> dict[str, int]:
    scopes = conn.execute(
        f"""
        SELECT ss.snapshot_set_id, ss.statement_id, ss.account_id, ss.as_of_date,
               ss.currency, ss.section_type, ss.scope_key, ss.completeness,
               ss.reported_total, s.ingestion_run_id
          FROM snapshot_sets ss
          JOIN statements s ON s.statement_id = ss.statement_id
         WHERE ss.reported_total IS NOT NULL
           AND {canonical_statement_clause("ss.statement_id")}
         ORDER BY ss.statement_id, ss.snapshot_set_id
        """
    ).fetchall()
    metrics: Counter[str] = Counter()

    for scope in scopes:
        expected_close: float | None = None
        reason: str | None = None
        if scope["completeness"] != "complete":
            status = "incomplete_input"
            reason = "reported total belongs to an incomplete scope"
        elif scope["section_type"] == "positions":
            rows = conn.execute(
                """
                SELECT market_value
                  FROM position_snapshots
                 WHERE snapshot_set_id = ?
                """,
                (scope["snapshot_set_id"],),
            ).fetchall()
            if any(row["market_value"] is None for row in rows):
                status = "incomplete_input"
                reason = "position scope has market values unavailable"
            else:
                expected_close = sum(float(row["market_value"]) for row in rows)
                status = _result_status(
                    float(scope["reported_total"]) - expected_close,
                    CASH_TOLERANCE,
                )
        elif scope["section_type"] == "cash":
            cash = _cash_balance_for_scope(conn, int(scope["snapshot_set_id"]))
            if cash is None:
                status = "incomplete_input"
                reason = "cash scope has no closing balance row"
            else:
                expected_close = float(cash["closing_balance"])
                status = _result_status(
                    float(scope["reported_total"]) - expected_close,
                    CASH_TOLERANCE,
                )
        else:
            component_scopes = conn.execute(
                """
                SELECT snapshot_set_id, section_type, completeness
                  FROM snapshot_sets
                 WHERE statement_id = ?
                   AND currency = ?
                   AND scope_key = ?
                   AND section_type IN ('positions', 'cash')
                """,
                (scope["statement_id"], scope["currency"], scope["scope_key"]),
            ).fetchall()
            if not component_scopes:
                status = "not_applicable"
                reason = "summary scope has no matching position or cash scope"
            elif any(row["completeness"] != "complete" for row in component_scopes):
                status = "incomplete_input"
                reason = "summary scope has an incomplete component scope"
            else:
                position_scope_ids = [
                    int(row["snapshot_set_id"])
                    for row in component_scopes
                    if row["section_type"] == "positions"
                ]
                cash_scope_ids = [
                    int(row["snapshot_set_id"])
                    for row in component_scopes
                    if row["section_type"] == "cash"
                ]
                position_rows = conn.execute(
                    f"""
                    SELECT market_value
                      FROM position_snapshots
                     WHERE snapshot_set_id IN ({','.join('?' * len(position_scope_ids))})
                    """
                    if position_scope_ids
                    else "SELECT NULL AS market_value WHERE 0",
                    position_scope_ids,
                ).fetchall()
                cash_rows = conn.execute(
                    f"""
                    SELECT ss.snapshot_set_id, cb.closing_balance
                      FROM snapshot_sets ss
                      LEFT JOIN cash_balances cb ON cb.snapshot_set_id = ss.snapshot_set_id
                     WHERE ss.snapshot_set_id IN ({','.join('?' * len(cash_scope_ids))})
                    """
                    if cash_scope_ids
                    else "SELECT NULL AS snapshot_set_id, NULL AS closing_balance WHERE 0",
                    cash_scope_ids,
                ).fetchall()
                if any(row["market_value"] is None for row in position_rows):
                    status = "incomplete_input"
                    reason = "summary scope has unavailable position market values"
                elif any(row["closing_balance"] is None for row in cash_rows):
                    status = "incomplete_input"
                    reason = "summary scope has a cash scope without a closing balance"
                else:
                    expected_close = sum(
                        float(row["market_value"]) for row in position_rows
                    ) + sum(float(row["closing_balance"]) for row in cash_rows)
                    status = _result_status(
                        float(scope["reported_total"]) - expected_close,
                        CASH_TOLERANCE,
                    )

        reported_close = float(scope["reported_total"])
        residual = reported_close - expected_close if expected_close is not None else None
        if residual is not None and reason is None:
            reason = _residual_reason(residual, CASH_TOLERANCE)
        _write_reconciliation_result(
            conn,
            reconciliation_key=f"{RECONCILIATION_KEY_PREFIX}total:{scope['snapshot_set_id']}",
            ingestion_run_id=scope["ingestion_run_id"],
            kind="statement_total",
            account_id=scope["account_id"],
            statement_id=scope["statement_id"],
            snapshot_set_id=scope["snapshot_set_id"],
            prior_snapshot_set_id=None,
            instrument_id=None,
            currency=scope["currency"],
            prior_checkpoint=None,
            current_checkpoint=scope["as_of_date"],
            opening_value=None,
            summed_deltas=None,
            expected_close=expected_close,
            reported_close=reported_close,
            residual=residual,
            tolerance=CASH_TOLERANCE,
            status=status,
            reason=reason,
        )
        _add_result_metric(metrics, status)
    return dict(metrics)


def rebuild_reconciliation_results(path: Path | str | None = None) -> dict[str, object]:
    """Rebuild generated equations without changing ledger facts or balances."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        cleared = int(
            conn.execute(
                "DELETE FROM reconciliation_results WHERE reconciliation_key LIKE ?",
                (f"{RECONCILIATION_KEY_PREFIX}%",),
            ).rowcount
            or 0
        )
        return {
            "cleared": cleared,
            "positions": _reconcile_position_scopes(conn),
            "cash": _reconcile_cash_scopes(conn),
            "statement_totals": _reconcile_statement_totals(conn),
        }


def reconcile_after_ingest(path: Path | str | None = None) -> dict:
    """Run all automatic reconciliation passes."""
    return {
        "instrument_names": resolve_trade_instruments_from_holdings(path),
        "transfers": link_transfers(path),
        "positions": rebuild_position_transaction_links(path),
        "results": rebuild_reconciliation_results(path),
    }

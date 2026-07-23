"""CLI-only, non-destructive shadow-ledger rebuild support.

The functions in this module never open the source ledger for writing.  A
shadow is first rebuilt into a fresh staging database, then promoted only after
the optional second rebuild has the same deterministic content fingerprint.
Cutover is a separately guarded operation and is deliberately not part of a
rebuild.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .db import sqlite as sqlite_db
from .identity import canonical_instrument_key
from .ingest.initials import infer_initials
from .ingest.pipeline import run_ingest
from .ingest.reconcile import reconcile_after_ingest

SHADOW_REPORT_VERSION = 2
GENERATED_RECONCILIATION_PREFIX = "recon:v1:"


@dataclass
class CuratedState:
    """User-owned state exported from a source ledger without mutating it."""

    accounts: list[dict] = field(default_factory=list)
    instruments: dict[str, dict] = field(default_factory=dict)
    aliases: list[dict] = field(default_factory=list)
    resolution_candidates: list[dict] = field(default_factory=list)
    market_symbols: list[dict] = field(default_factory=list)
    journal_pairs: list[dict] = field(default_factory=list)
    ticker_changes: list[dict] = field(default_factory=list)
    identifier_lookups: list[dict] = field(default_factory=list)
    initial_positions: list[dict] = field(default_factory=list)
    initial_cash: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    annotation_components: int = 0
    unmapped_annotations: int = 0
    unavailable: dict[str, str] = field(default_factory=dict)
    config_bytes: bytes | None = None
    config_sha256: str | None = None
    config_account_ids: int = 0
    unmapped_config_account_ids: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "accounts": len(self.accounts),
            "aliases": len(self.aliases),
            "resolution_candidates": len(self.resolution_candidates),
            "market_symbols": len(self.market_symbols),
            "journal_pairs": len(self.journal_pairs),
            "ticker_changes": len(self.ticker_changes),
            "identifier_lookups": len(self.identifier_lookups),
            "initial_positions": len(self.initial_positions),
            "initial_cash": len(self.initial_cash),
            "annotations": len(self.annotations),
            "annotation_components": self.annotation_components,
            "unmapped_annotations": self.unmapped_annotations,
            "portfolio_config": 1 if self.config_bytes is not None else 0,
            "portfolio_account_ids": self.config_account_ids,
            "unmapped_portfolio_account_ids": self.unmapped_config_account_ids,
        }


RebuildRunner = Callable[[Path, Path, Path, Path], dict[str, object] | None]


@contextmanager
def _readonly_connection(path: Path | str):
    source = Path(path).resolve(strict=True)
    wal = source.with_name(source.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError(
            f"refusing an unstable SQLite source with a non-empty WAL: {source.name}; "
            "stop/checkpoint its writer before building a shadow"
        )
    # immutable=1 avoids creating a shared-memory sidecar while inspecting a
    # stable source or completed staging database. A non-empty WAL is rejected
    # above because immutable connections intentionally do not consume it.
    conn = sqlite3.connect(f"{source.as_uri()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _row_value(row: sqlite3.Row, column: str, default=None):
    return row[column] if column in row.keys() else default


def _instrument_record(row: sqlite3.Row) -> dict:
    asset_type = str(row["asset_type"])
    symbol = str(row["symbol"])
    currency = str(row["currency"])
    option_multiplier = int(_row_value(row, "option_multiplier", 100) or 100)
    instrument_key = _row_value(row, "instrument_key") or canonical_instrument_key(
        asset_type=asset_type,
        symbol=symbol,
        currency=currency,
        option_root=_row_value(row, "option_root"),
        option_expiry=_row_value(row, "option_expiry"),
        option_strike=_row_value(row, "option_strike"),
        option_type=_row_value(row, "option_type"),
        option_multiplier=option_multiplier,
    )
    return {
        "instrument_id": int(row["instrument_id"]),
        "instrument_key": str(instrument_key),
        "asset_type": asset_type,
        "symbol": symbol,
        "currency": currency,
        "exchange": _row_value(row, "exchange"),
        "name": _row_value(row, "name"),
        "option_root": _row_value(row, "option_root"),
        "option_expiry": _row_value(row, "option_expiry"),
        "option_strike": _row_value(row, "option_strike"),
        "option_type": _row_value(row, "option_type"),
        "option_multiplier": option_multiplier,
        "resolution_method": _row_value(row, "resolution_method"),
        "resolution_confidence": _row_value(row, "resolution_confidence"),
    }


def _account_locator(row: sqlite3.Row) -> tuple[str, str]:
    return (str(row["institution_code"]), str(row["account_number"]))


def _load_source_instruments(conn: sqlite3.Connection) -> dict[int, dict]:
    required = {"instrument_id", "asset_type", "symbol", "currency"}
    if not required.issubset(_columns(conn, "instruments")):
        return {}
    rows = conn.execute("SELECT * FROM instruments").fetchall()
    return {int(row["instrument_id"]): _instrument_record(row) for row in rows}


def export_curated_state(source_db: Path | str) -> CuratedState:
    """Export only explicit user/reviewed state from ``source_db`` read-only.

    Derived transactions, snapshots, inferred initials, automatic links, and
    generated reconciliation rows are intentionally excluded.  If an old
    source schema cannot prove that an item is curated, the item is reported as
    unavailable rather than guessed or copied.
    """
    state = CuratedState()
    source_path = Path(source_db).resolve(strict=True)
    config_path = source_path.parent / "config.json"
    if config_path.is_file():
        state.config_bytes = config_path.read_bytes()
        state.config_sha256 = hashlib.sha256(state.config_bytes).hexdigest()

    with _readonly_connection(source_path) as conn:
        account_columns = _columns(conn, "accounts")
        if {"account_id", "institution_id", "account_number"}.issubset(account_columns) and _table_exists(
            conn, "institutions"
        ):
            opened_on_sql = "a.opened_on" if "opened_on" in account_columns else "NULL AS opened_on"
            closed_on_sql = "a.closed_on" if "closed_on" in account_columns else "NULL AS closed_on"
            notes_sql = "a.notes" if "notes" in account_columns else "NULL AS notes"
            rows = conn.execute(
                f"""
                SELECT a.account_id, a.account_number, a.account_type, a.nickname,
                       a.base_currency, {opened_on_sql}, {closed_on_sql}, {notes_sql},
                       i.code AS institution_code,
                       i.display_name AS institution_name
                  FROM accounts a
                  JOIN institutions i ON i.institution_id = a.institution_id
                 ORDER BY i.code, a.account_number
                """
            ).fetchall()
            state.accounts = [dict(row) for row in rows]
        else:
            state.unavailable["accounts"] = "source schema has no usable accounts/institutions tables"

        account_by_id = {
            int(row["account_id"]): _account_locator(row)
            for row in state.accounts
        }
        if state.config_bytes is not None:
            try:
                config_payload = json.loads(state.config_bytes)
            except (TypeError, ValueError, UnicodeDecodeError):
                state.unavailable["portfolio_config"] = "config.json is not valid JSON"
            else:
                portfolios = config_payload.get("portfolios", []) if isinstance(config_payload, dict) else []
                if not isinstance(portfolios, list):
                    state.unavailable["portfolio_config"] = "config portfolios are not a list"
                else:
                    for portfolio in portfolios:
                        if not isinstance(portfolio, dict):
                            continue
                        account_ids = portfolio.get("account_ids", [])
                        if not isinstance(account_ids, list):
                            state.unavailable["portfolio_config"] = "a portfolio account_ids value is not a list"
                            continue
                        for account_id in account_ids:
                            if isinstance(account_id, int) and not isinstance(account_id, bool):
                                state.config_account_ids += 1
                                if account_id not in account_by_id:
                                    state.unmapped_config_account_ids += 1
                            else:
                                state.unmapped_config_account_ids += 1
                    if state.unmapped_config_account_ids:
                        state.unavailable["portfolio_config"] = (
                            "one or more portfolio account IDs do not map to a source account"
                        )
        instruments_by_id = _load_source_instruments(conn)

        def remember_instrument(instrument_id: int | None) -> str | None:
            if instrument_id is None:
                return None
            record = instruments_by_id.get(int(instrument_id))
            if record is None:
                return None
            key = str(record["instrument_key"])
            state.instruments[key] = record
            return key

        alias_columns = _columns(conn, "instrument_aliases")
        if {"instrument_id", "alias", "institution_id"}.issubset(alias_columns):
            rows = conn.execute(
                """
                SELECT ia.alias, ia.instrument_id, scoped.code AS institution_code
                  FROM instrument_aliases ia
                  LEFT JOIN institutions scoped ON scoped.institution_id = ia.institution_id
                 ORDER BY ia.alias, COALESCE(scoped.code, '')
                """
            ).fetchall()
            for row in rows:
                key = remember_instrument(row["instrument_id"])
                if key is None:
                    state.unavailable["aliases"] = "one or more aliases reference missing instruments"
                    continue
                state.aliases.append(
                    {
                        "alias": str(row["alias"]),
                        "institution_code": row["institution_code"],
                        "instrument_key": key,
                    }
                )
        elif _table_exists(conn, "instrument_aliases"):
            state.unavailable["aliases"] = "source aliases table has an unsupported schema"

        candidate_columns = _columns(conn, "instrument_resolution_candidates")
        if {"institution_id", "resolved_instrument_id", "status"}.issubset(candidate_columns):
            rows = conn.execute(
                """
                SELECT candidate.*, institution.code AS institution_code
                  FROM instrument_resolution_candidates candidate
                  JOIN institutions institution
                    ON institution.institution_id = candidate.institution_id
                 WHERE candidate.status = 'resolved'
                   AND candidate.resolved_instrument_id IS NOT NULL
                 ORDER BY candidate.candidate_id
                """
            ).fetchall()
            for row in rows:
                key = remember_instrument(row["resolved_instrument_id"])
                if key is None:
                    state.unavailable["resolution_candidates"] = (
                        "one or more resolved candidates reference missing instruments"
                    )
                    continue
                record = dict(row)
                record["instrument_key"] = key
                state.resolution_candidates.append(record)

        market_columns = _columns(conn, "instrument_market_symbols")
        if {"instrument_id", "provider", "provider_symbol", "status"}.issubset(market_columns):
            rows = conn.execute(
                """
                SELECT * FROM instrument_market_symbols
                 WHERE status = 'verified' ORDER BY market_symbol_id
                """
            ).fetchall()
            for row in rows:
                key = remember_instrument(row["instrument_id"])
                if key is None:
                    state.unavailable["market_symbols"] = (
                        "one or more verified market symbols reference missing instruments"
                    )
                    continue
                record = dict(row)
                record["instrument_key"] = key
                state.market_symbols.append(record)

        journal_columns = _columns(conn, "instrument_journal_pairs")
        if {"from_instrument_id", "to_instrument_id", "status"}.issubset(journal_columns):
            rows = conn.execute(
                """
                SELECT * FROM instrument_journal_pairs
                 WHERE status = 'reviewed' ORDER BY journal_pair_id
                """
            ).fetchall()
            for row in rows:
                old_key = remember_instrument(row["from_instrument_id"])
                new_key = remember_instrument(row["to_instrument_id"])
                if old_key is None or new_key is None:
                    state.unavailable["journal_pairs"] = (
                        "one or more reviewed journal pairs reference missing instruments"
                    )
                    continue
                record = dict(row)
                record["from_instrument_key"] = old_key
                record["to_instrument_key"] = new_key
                state.journal_pairs.append(record)

        ticker_change_columns = _columns(conn, "instrument_ticker_changes")
        required_ticker_change_columns = {
            "from_instrument_id",
            "to_instrument_id",
            "effective_date",
            "conversion_ratio",
            "status",
            "resolution_method",
            "resolution_confidence",
        }
        if required_ticker_change_columns.issubset(ticker_change_columns):
            rows = conn.execute(
                """
                SELECT * FROM instrument_ticker_changes
                 WHERE status = 'reviewed'
                 ORDER BY effective_date, ticker_change_id
                """
            ).fetchall()
            for row in rows:
                old_key = remember_instrument(row["from_instrument_id"])
                new_key = remember_instrument(row["to_instrument_id"])
                if old_key is None or new_key is None:
                    state.unavailable["ticker_changes"] = (
                        "one or more reviewed ticker changes reference missing instruments"
                    )
                    continue
                state.ticker_changes.append(
                    {
                        "from_instrument_key": old_key,
                        "to_instrument_key": new_key,
                        "effective_date": str(row["effective_date"]),
                        "conversion_ratio": row["conversion_ratio"],
                        "resolution_method": str(row["resolution_method"]),
                        "resolution_confidence": row["resolution_confidence"],
                        "notes": row["notes"] if "notes" in row.keys() else None,
                    }
                )
        elif _table_exists(conn, "instrument_ticker_changes"):
            state.unavailable["ticker_changes"] = (
                "source ticker-change table has an unsupported schema"
            )

        lookup_columns = _columns(conn, "instrument_identifier_lookups")
        required_lookup_columns = {
            "identifier_type",
            "asset_type",
            "institution_code",
            "normalized_name",
            "display_name",
            "currency",
            "status",
        }
        if required_lookup_columns.issubset(lookup_columns):
            rows = conn.execute(
                "SELECT * FROM instrument_identifier_lookups WHERE status <> 'pending'"
            ).fetchall()
            state.identifier_lookups = [dict(row) for row in rows]
        elif _table_exists(conn, "instrument_identifier_lookups"):
            state.unavailable["identifier_lookups"] = "source lookup table has an unsupported schema"

        position_columns = _columns(conn, "initial_positions")
        required_position_columns = {
            "account_id",
            "as_of_date",
            "instrument_id",
            "quantity",
            "currency",
        }
        if required_position_columns.issubset(position_columns):
            notes_sql = "notes" if "notes" in position_columns else "NULL AS notes"
            rows = conn.execute(
                f"SELECT account_id, as_of_date, instrument_id, quantity, avg_cost, currency, {notes_sql} "
                "FROM initial_positions"
            ).fetchall()
            for row in rows:
                # Position inference has always tagged only inferred rows, so
                # a NULL note remains a conservatively preserved manual row.
                if row["notes"] is not None and str(row["notes"]).startswith("inferred:"):
                    continue
                locator = account_by_id.get(int(row["account_id"]))
                key = remember_instrument(row["instrument_id"])
                if locator is None or key is None:
                    state.unavailable["initial_positions"] = "one or more manual rows lack an account/instrument"
                    continue
                state.initial_positions.append(
                    {
                        "account": locator,
                        "as_of_date": str(row["as_of_date"]),
                        "instrument_key": key,
                        "quantity": row["quantity"],
                        "avg_cost": row["avg_cost"],
                        "currency": str(row["currency"]),
                        "notes": row["notes"],
                    }
                )
        elif _table_exists(conn, "initial_positions"):
            state.unavailable["initial_positions"] = "source initial_positions table has an unsupported schema"

        cash_columns = _columns(conn, "initial_cash")
        required_cash_columns = {"account_id", "as_of_date", "currency", "balance"}
        if required_cash_columns.issubset(cash_columns):
            if "notes" not in cash_columns:
                state.unavailable["initial_cash"] = "legacy untagged cash initials are inferred, not copied"
            else:
                rows = conn.execute(
                    """
                    SELECT account_id, as_of_date, currency, balance, notes
                      FROM initial_cash
                     WHERE notes IS NOT NULL AND notes NOT LIKE 'inferred:%'
                    """
                ).fetchall()
                for row in rows:
                    locator = account_by_id.get(int(row["account_id"]))
                    if locator is None:
                        state.unavailable["initial_cash"] = "one or more manual cash rows lack an account"
                        continue
                    state.initial_cash.append(
                        {
                            "account": locator,
                            "as_of_date": str(row["as_of_date"]),
                            "currency": str(row["currency"]),
                            "balance": row["balance"],
                            "notes": row["notes"],
                        }
                    )
        elif _table_exists(conn, "initial_cash"):
            state.unavailable["initial_cash"] = "source initial_cash table has an unsupported schema"

        _export_reviewed_annotations(conn, state, account_by_id, instruments_by_id, remember_instrument)
    return state


def _export_reviewed_annotations(
    conn: sqlite3.Connection,
    state: CuratedState,
    account_by_id: dict[int, tuple[str, str]],
    instruments_by_id: dict[int, dict],
    remember_instrument: Callable[[int | None], str | None],
) -> None:
    required = {
        "reconciliation_key",
        "kind",
        "account_id",
        "statement_id",
        "snapshot_set_id",
        "prior_snapshot_set_id",
        "instrument_id",
        "currency",
        "tolerance",
        "status",
    }
    if not required.issubset(_columns(conn, "reconciliation_results")):
        if _table_exists(conn, "reconciliation_results"):
            state.unavailable["annotations"] = "source reconciliation table has an unsupported schema"
        return
    if not {"statement_id", "statement_key"}.issubset(_columns(conn, "statements")):
        state.unavailable["annotations"] = "source annotations lack stable statement keys"
        return
    if not {"snapshot_set_id", "statement_id", "currency", "section_type", "scope_key"}.issubset(
        _columns(conn, "snapshot_sets")
    ):
        state.unavailable["annotations"] = "source annotations lack stable snapshot scope keys"
        return

    statement_keys = {
        int(row["statement_id"]): str(row["statement_key"])
        for row in conn.execute("SELECT statement_id, statement_key FROM statements")
    }
    snapshot_keys = {
        int(row["snapshot_set_id"]): {
            "statement_key": statement_keys.get(int(row["statement_id"])),
            "currency": str(row["currency"]),
            "section_type": str(row["section_type"]),
            "scope_key": str(row["scope_key"]),
        }
        for row in conn.execute(
            "SELECT snapshot_set_id, statement_id, currency, section_type, scope_key FROM snapshot_sets"
        )
    }
    evidence_keys: dict[int, str] = {}
    if {"transaction_id", "evidence_id"}.issubset(_columns(conn, "transactions")) and {
        "evidence_id",
        "evidence_key",
    }.issubset(_columns(conn, "source_evidence")):
        evidence_keys = {
            int(row["transaction_id"]): str(row["evidence_key"])
            for row in conn.execute(
                """
                SELECT t.transaction_id, e.evidence_key
                  FROM transactions t
                  JOIN source_evidence e ON e.evidence_id = t.evidence_id
                """
            )
        }
    components_by_result: dict[int, list[dict]] = {}
    if {"reconciliation_id", "transaction_id", "delta"}.issubset(
        _columns(conn, "reconciliation_components")
    ):
        for row in conn.execute(
            "SELECT reconciliation_id, transaction_id, delta FROM reconciliation_components"
        ):
            key = evidence_keys.get(int(row["transaction_id"]))
            if key is None:
                continue
            components_by_result.setdefault(int(row["reconciliation_id"]), []).append(
                {"transaction_evidence_key": key, "delta": row["delta"]}
            )

    rows = conn.execute(
        "SELECT * FROM reconciliation_results WHERE reconciliation_key NOT LIKE ?",
        (f"{GENERATED_RECONCILIATION_PREFIX}%",),
    ).fetchall()
    for row in rows:
        account = account_by_id.get(int(row["account_id"]))
        statement_key = statement_keys.get(int(row["statement_id"])) if row["statement_id"] else None
        snapshot = snapshot_keys.get(int(row["snapshot_set_id"])) if row["snapshot_set_id"] else None
        prior_snapshot = (
            snapshot_keys.get(int(row["prior_snapshot_set_id"])) if row["prior_snapshot_set_id"] else None
        )
        instrument_key = remember_instrument(row["instrument_id"])
        if account is None or (row["statement_id"] and statement_key is None):
            state.unmapped_annotations += 1
            continue
        if snapshot is not None and snapshot["statement_key"] is None:
            state.unmapped_annotations += 1
            continue
        if prior_snapshot is not None and prior_snapshot["statement_key"] is None:
            state.unmapped_annotations += 1
            continue
        if row["instrument_id"] is not None and instrument_key is None:
            state.unmapped_annotations += 1
            continue
        item = {
            "reconciliation_key": str(row["reconciliation_key"]),
            "kind": str(row["kind"]),
            "account": account,
            "statement_key": statement_key,
            "snapshot": snapshot,
            "prior_snapshot": prior_snapshot,
            "instrument_key": instrument_key,
            "currency": str(row["currency"]),
            "prior_checkpoint": _row_value(row, "prior_checkpoint"),
            "current_checkpoint": _row_value(row, "current_checkpoint"),
            "opening_value": _row_value(row, "opening_value"),
            "summed_deltas": _row_value(row, "summed_deltas"),
            "expected_close": _row_value(row, "expected_close"),
            "reported_close": _row_value(row, "reported_close"),
            "residual": _row_value(row, "residual"),
            "tolerance": row["tolerance"],
            "status": str(row["status"]),
            "reason": _row_value(row, "reason"),
            "components": components_by_result.get(int(row["reconciliation_id"]), []),
        }
        state.annotations.append(item)
        state.annotation_components += len(item["components"])


def _account_id(conn: sqlite3.Connection, locator: tuple[str, str]) -> int | None:
    row = conn.execute(
        """
        SELECT a.account_id
          FROM accounts a
          JOIN institutions i ON i.institution_id = a.institution_id
         WHERE i.code = ? AND a.account_number = ?
        """,
        locator,
    ).fetchone()
    return int(row["account_id"]) if row is not None else None


def _upsert_instrument_record(conn: sqlite3.Connection, record: dict) -> int:
    return sqlite_db.upsert_instrument(
        conn,
        asset_type=str(record["asset_type"]),
        symbol=str(record["symbol"]),
        currency=str(record["currency"]),
        exchange=record["exchange"],
        name=record["name"],
        option_root=record["option_root"],
        option_expiry=record["option_expiry"],
        option_strike=record["option_strike"],
        option_type=record["option_type"],
        option_multiplier=int(record["option_multiplier"]),
        resolution_method=record["resolution_method"],
        resolution_confidence=record["resolution_confidence"],
    )


def _upsert_shadow_account(
    conn: sqlite3.Connection,
    *,
    source_account_id: int,
    institution_id: int,
    account: dict,
) -> int:
    """Create a source account identity without changing its config ID.

    Portfolio preferences currently reference numeric account IDs.  A fresh
    shadow database can safely retain those IDs because accounts are imported
    before parsing any source facts.  A collision is a hard failure rather
    than a guessed remap.
    """
    existing = conn.execute(
        "SELECT account_id FROM accounts WHERE institution_id = ? AND account_number = ?",
        (institution_id, str(account["account_number"])),
    ).fetchone()
    if existing is not None:
        existing_id = int(existing["account_id"])
        if existing_id != source_account_id:
            raise RuntimeError(
                "shadow account identity collision; refusing to remap portfolio configuration"
            )
        sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number=str(account["account_number"]),
            account_type=account["account_type"],
            nickname=account["nickname"],
            base_currency=str(account["base_currency"] or "CAD"),
            opened_on=account["opened_on"],
            closed_on=account["closed_on"],
            notes=account["notes"],
        )
        return existing_id

    id_collision = conn.execute(
        "SELECT 1 FROM accounts WHERE account_id = ?", (source_account_id,)
    ).fetchone()
    if id_collision is not None:
        raise RuntimeError("shadow account ID collision; refusing to remap portfolio configuration")
    conn.execute(
        """
        INSERT INTO accounts(
            account_id, institution_id, account_number, account_type, nickname,
            base_currency, opened_on, closed_on, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_account_id,
            institution_id,
            str(account["account_number"]),
            account["account_type"],
            account["nickname"],
            str(account["base_currency"] or "CAD"),
            account["opened_on"],
            account["closed_on"],
            account["notes"],
        ),
    )
    return source_account_id


def import_identity_state(target_db: Path | str, state: CuratedState) -> dict[str, int]:
    """Import account metadata and reviewed identity inputs before re-ingest."""
    summary = {
        "accounts": 0,
        "account_ids_preserved": 0,
        "instruments": 0,
        "aliases": 0,
        "resolution_candidates": 0,
        "market_symbols": 0,
        "journal_pairs": 0,
        "ticker_changes": 0,
        "identifier_lookups": 0,
    }
    sqlite_db.init_db(target_db)
    with sqlite_db.session(target_db) as conn:
        for account in state.accounts:
            institution_id = sqlite_db.upsert_institution(
                conn,
                str(account["institution_code"]),
                str(account["institution_name"]),
            )
            target_account_id = _upsert_shadow_account(
                conn,
                source_account_id=int(account["account_id"]),
                institution_id=institution_id,
                account=account,
            )
            summary["accounts"] += 1
            if target_account_id == int(account["account_id"]):
                summary["account_ids_preserved"] += 1

        for record in state.instruments.values():
            _upsert_instrument_record(conn, record)
            summary["instruments"] += 1

        for alias in state.aliases:
            instrument = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (alias["instrument_key"],),
            ).fetchone()
            if instrument is None:
                continue
            institution_id = None
            if alias["institution_code"] is not None:
                institution_id = sqlite_db.upsert_institution(
                    conn,
                    str(alias["institution_code"]),
                    str(alias["institution_code"]),
                )
            existing = conn.execute(
                """
                SELECT alias_id FROM instrument_aliases
                 WHERE alias = ?
                   AND ((institution_id = ?) OR (? IS NULL AND institution_id IS NULL))
                """,
                (alias["alias"], institution_id, institution_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO instrument_aliases(instrument_id, alias, institution_id) VALUES (?, ?, ?)",
                    (instrument["instrument_id"], alias["alias"], institution_id),
                )
            else:
                conn.execute(
                    "UPDATE instrument_aliases SET instrument_id = ? WHERE alias_id = ?",
                    (instrument["instrument_id"], existing["alias_id"]),
                )
            summary["aliases"] += 1

        for candidate in state.resolution_candidates:
            instrument = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (candidate["instrument_key"],),
            ).fetchone()
            if instrument is None:
                continue
            institution_id = sqlite_db.upsert_institution(
                conn,
                str(candidate["institution_code"]),
                str(candidate["institution_code"]),
            )
            conn.execute(
                """
                INSERT INTO instrument_resolution_candidates(
                    institution_id, normalized_text, display_text, asset_type,
                    currency, status, resolved_instrument_id, resolution_method,
                    resolution_confidence, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'resolved', ?, ?, ?, ?, ?)
                ON CONFLICT(institution_id, normalized_text, asset_type, currency)
                DO UPDATE SET
                    display_text = excluded.display_text,
                    status = 'resolved',
                    resolved_instrument_id = excluded.resolved_instrument_id,
                    resolution_method = excluded.resolution_method,
                    resolution_confidence = excluded.resolution_confidence,
                    first_seen_at = excluded.first_seen_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    institution_id,
                    candidate["normalized_text"],
                    candidate["display_text"],
                    candidate["asset_type"],
                    candidate["currency"],
                    instrument["instrument_id"],
                    candidate.get("resolution_method"),
                    candidate.get("resolution_confidence"),
                    candidate["first_seen_at"],
                    candidate["last_seen_at"],
                ),
            )
            summary["resolution_candidates"] += 1

        for market_symbol in state.market_symbols:
            instrument = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (market_symbol["instrument_key"],),
            ).fetchone()
            if instrument is None:
                continue
            conn.execute(
                """
                INSERT INTO instrument_market_symbols(
                    instrument_id, provider, provider_symbol, status,
                    last_checked_at, verified_at, last_error
                ) VALUES (?, ?, ?, 'verified', ?, ?, NULL)
                ON CONFLICT(instrument_id, provider) DO UPDATE SET
                    provider_symbol = excluded.provider_symbol,
                    status = 'verified',
                    last_checked_at = excluded.last_checked_at,
                    verified_at = excluded.verified_at,
                    last_error = NULL
                """,
                (
                    instrument["instrument_id"],
                    market_symbol["provider"],
                    market_symbol["provider_symbol"],
                    market_symbol.get("last_checked_at"),
                    market_symbol.get("verified_at"),
                ),
            )
            summary["market_symbols"] += 1

        for pair in state.journal_pairs:
            old = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (pair["from_instrument_key"],),
            ).fetchone()
            new = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (pair["to_instrument_key"],),
            ).fetchone()
            if old is None or new is None:
                continue
            existing = conn.execute(
                """
                SELECT journal_pair_id FROM instrument_journal_pairs
                 WHERE from_instrument_id = ? AND to_instrument_id = ?
                   AND ((effective_from = ?) OR
                        (effective_from IS NULL AND ? IS NULL))
                """,
                (
                    old["instrument_id"],
                    new["instrument_id"],
                    pair.get("effective_from"),
                    pair.get("effective_from"),
                ),
            ).fetchone()
            values = (
                pair["conversion_ratio"],
                pair.get("effective_to"),
                pair.get("notes"),
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO instrument_journal_pairs(
                        from_instrument_id, to_instrument_id, conversion_ratio,
                        status, effective_from, effective_to, notes
                    ) VALUES (?, ?, ?, 'reviewed', ?, ?, ?)
                    """,
                    (
                        old["instrument_id"],
                        new["instrument_id"],
                        pair["conversion_ratio"],
                        pair.get("effective_from"),
                        pair.get("effective_to"),
                        pair.get("notes"),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE instrument_journal_pairs
                       SET conversion_ratio = ?, status = 'reviewed',
                           effective_to = ?, notes = ?
                     WHERE journal_pair_id = ?
                    """,
                    (*values, existing["journal_pair_id"]),
                )
            summary["journal_pairs"] += 1

        for change in state.ticker_changes:
            old = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (change["from_instrument_key"],),
            ).fetchone()
            new = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (change["to_instrument_key"],),
            ).fetchone()
            if old is None or new is None:
                continue
            conn.execute(
                """
                INSERT INTO instrument_ticker_changes(
                    from_instrument_id, to_instrument_id, effective_date,
                    conversion_ratio, status, resolution_method,
                    resolution_confidence, notes
                ) VALUES (?, ?, ?, ?, 'reviewed', ?, ?, ?)
                ON CONFLICT(from_instrument_id, to_instrument_id, effective_date)
                DO UPDATE SET
                    conversion_ratio = excluded.conversion_ratio,
                    status = 'reviewed',
                    resolution_method = excluded.resolution_method,
                    resolution_confidence = excluded.resolution_confidence,
                    notes = excluded.notes
                """,
                (
                    old["instrument_id"],
                    new["instrument_id"],
                    change["effective_date"],
                    change["conversion_ratio"],
                    change["resolution_method"],
                    change["resolution_confidence"],
                    change.get("notes"),
                ),
            )
            summary["ticker_changes"] += 1

        for lookup in state.identifier_lookups:
            conn.execute(
                """
                INSERT INTO instrument_identifier_lookups(
                    identifier_type, asset_type, institution_code, normalized_name,
                    display_name, currency, status, resolved_symbol, resolved_exchange,
                    resolved_name, evidence_url, sample_description, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identifier_type, asset_type, institution_code, normalized_name, currency)
                DO UPDATE SET
                    display_name = excluded.display_name,
                    status = excluded.status,
                    resolved_symbol = excluded.resolved_symbol,
                    resolved_exchange = excluded.resolved_exchange,
                    resolved_name = excluded.resolved_name,
                    evidence_url = excluded.evidence_url,
                    sample_description = excluded.sample_description,
                    notes = excluded.notes,
                    last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                """,
                (
                    lookup["identifier_type"],
                    lookup["asset_type"],
                    lookup["institution_code"],
                    lookup["normalized_name"],
                    lookup["display_name"],
                    lookup["currency"],
                    lookup["status"],
                    lookup.get("resolved_symbol"),
                    lookup.get("resolved_exchange"),
                    lookup.get("resolved_name"),
                    lookup.get("evidence_url"),
                    lookup.get("sample_description"),
                    lookup.get("notes"),
                ),
            )
            summary["identifier_lookups"] += 1
    return summary


def import_manual_initials(target_db: Path | str, state: CuratedState) -> dict[str, int]:
    """Copy tagged/manual initial anchors after fresh snapshots are available."""
    summary = {"initial_positions": 0, "initial_cash": 0, "skipped": 0}
    with sqlite_db.session(target_db) as conn:
        for row in state.initial_positions:
            account_id = _account_id(conn, tuple(row["account"]))
            instrument = conn.execute(
                "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                (row["instrument_key"],),
            ).fetchone()
            if account_id is None or instrument is None:
                summary["skipped"] += 1
                continue
            conn.execute(
                """
                INSERT INTO initial_positions(
                    account_id, as_of_date, instrument_id, quantity, avg_cost, currency, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, as_of_date, instrument_id) DO UPDATE SET
                    quantity = excluded.quantity,
                    avg_cost = excluded.avg_cost,
                    currency = excluded.currency,
                    notes = excluded.notes
                """,
                (
                    account_id,
                    row["as_of_date"],
                    instrument["instrument_id"],
                    row["quantity"],
                    row["avg_cost"],
                    row["currency"],
                    row["notes"],
                ),
            )
            summary["initial_positions"] += 1
        for row in state.initial_cash:
            account_id = _account_id(conn, tuple(row["account"]))
            if account_id is None:
                summary["skipped"] += 1
                continue
            conn.execute(
                """
                INSERT INTO initial_cash(account_id, as_of_date, currency, balance, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, as_of_date, currency) DO UPDATE SET
                    balance = excluded.balance,
                    notes = excluded.notes
                """,
                (account_id, row["as_of_date"], row["currency"], row["balance"], row["notes"]),
            )
            summary["initial_cash"] += 1
    return summary


def _statement_id_for_key(conn: sqlite3.Connection, statement_key: str | None) -> int | None:
    if statement_key is None:
        return None
    row = conn.execute(
        "SELECT statement_id FROM statements WHERE statement_key = ?", (statement_key,)
    ).fetchone()
    return int(row["statement_id"]) if row is not None else None


def _snapshot_id_for_scope(conn: sqlite3.Connection, scope: dict | None) -> int | None:
    if scope is None:
        return None
    statement_id = _statement_id_for_key(conn, scope["statement_key"])
    if statement_id is None:
        return None
    row = conn.execute(
        """
        SELECT snapshot_set_id FROM snapshot_sets
         WHERE statement_id = ? AND currency = ? AND section_type = ? AND scope_key = ?
        """,
        (statement_id, scope["currency"], scope["section_type"], scope["scope_key"]),
    ).fetchone()
    return int(row["snapshot_set_id"]) if row is not None else None


def import_reviewed_annotations(target_db: Path | str, state: CuratedState) -> dict[str, int]:
    """Restore non-generated reconciliation annotations when every reference maps."""
    summary = {"annotations": 0, "components": 0, "skipped": 0, "component_skipped": 0}
    with sqlite_db.session(target_db) as conn:
        for row in state.annotations:
            account_id = _account_id(conn, tuple(row["account"]))
            statement_id = _statement_id_for_key(conn, row["statement_key"])
            snapshot_id = _snapshot_id_for_scope(conn, row["snapshot"])
            prior_snapshot_id = _snapshot_id_for_scope(conn, row["prior_snapshot"])
            instrument_id = None
            if row["instrument_key"] is not None:
                instrument = conn.execute(
                    "SELECT instrument_id FROM instruments WHERE instrument_key = ?",
                    (row["instrument_key"],),
                ).fetchone()
                instrument_id = int(instrument["instrument_id"]) if instrument else None
            if account_id is None or (row["statement_key"] is not None and statement_id is None):
                summary["skipped"] += 1
                continue
            if row["snapshot"] is not None and snapshot_id is None:
                summary["skipped"] += 1
                continue
            if row["prior_snapshot"] is not None and prior_snapshot_id is None:
                summary["skipped"] += 1
                continue
            if row["instrument_key"] is not None and instrument_id is None:
                summary["skipped"] += 1
                continue
            result = conn.execute(
                """
                INSERT INTO reconciliation_results(
                    reconciliation_key, ingestion_run_id, kind, account_id, statement_id,
                    snapshot_set_id, prior_snapshot_set_id, instrument_id, currency,
                    prior_checkpoint, current_checkpoint, opening_value, summed_deltas,
                    expected_close, reported_close, residual, tolerance, status, reason
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reconciliation_key) DO UPDATE SET
                    kind = excluded.kind,
                    account_id = excluded.account_id,
                    statement_id = excluded.statement_id,
                    snapshot_set_id = excluded.snapshot_set_id,
                    prior_snapshot_set_id = excluded.prior_snapshot_set_id,
                    instrument_id = excluded.instrument_id,
                    currency = excluded.currency,
                    prior_checkpoint = excluded.prior_checkpoint,
                    current_checkpoint = excluded.current_checkpoint,
                    opening_value = excluded.opening_value,
                    summed_deltas = excluded.summed_deltas,
                    expected_close = excluded.expected_close,
                    reported_close = excluded.reported_close,
                    residual = excluded.residual,
                    tolerance = excluded.tolerance,
                    status = excluded.status,
                    reason = excluded.reason
                RETURNING reconciliation_id
                """,
                (
                    row["reconciliation_key"],
                    row["kind"],
                    account_id,
                    statement_id,
                    snapshot_id,
                    prior_snapshot_id,
                    instrument_id,
                    row["currency"],
                    row["prior_checkpoint"],
                    row["current_checkpoint"],
                    row["opening_value"],
                    row["summed_deltas"],
                    row["expected_close"],
                    row["reported_close"],
                    row["residual"],
                    row["tolerance"],
                    row["status"],
                    row["reason"],
                ),
            ).fetchone()
            reconciliation_id = int(result["reconciliation_id"])
            summary["annotations"] += 1
            for component in row["components"]:
                transaction = conn.execute(
                    """
                    SELECT t.transaction_id
                      FROM transactions t
                      JOIN source_evidence e ON e.evidence_id = t.evidence_id
                     WHERE e.evidence_key = ?
                    """,
                    (component["transaction_evidence_key"],),
                ).fetchone()
                if transaction is None:
                    summary["component_skipped"] += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO reconciliation_components(reconciliation_id, transaction_id, delta)
                    VALUES (?, ?, ?)
                    ON CONFLICT(reconciliation_id, transaction_id) DO UPDATE SET delta = excluded.delta
                    """,
                    (reconciliation_id, transaction["transaction_id"], component["delta"]),
                )
                summary["components"] += 1
    return summary


def _table_counts(path: Path | str) -> dict[str, int]:
    tables = (
        "source_files",
        "statements",
        "transactions",
        "instrument_ticker_changes",
        "instrument_ticker_change_sources",
        "source_pages",
        "source_lines",
        "source_evidence_geometry",
        "source_evidence_lines",
        "position_snapshots",
        "cash_balances",
        "quarantine_transactions",
        "instruments",
        "security_issuers",
        "securities",
        "instrument_market_symbols",
        "instrument_journal_pairs",
        "instrument_resolution_candidates",
        "initial_positions",
        "initial_cash",
        "reconciliation_results",
    )
    with _readonly_connection(path) as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            if _table_exists(conn, table)
            else 0
            for table in tables
        }


def _content_hash(path: Path | str) -> str:
    """Fingerprint active ledger state without placing source values in reports.

    The source-run hashes prove the parser output.  The remaining semantic
    queries cover the persisted resolution, snapshot, reconciliation, and
    curated state which can change after parsing.  Database IDs and timestamps
    are deliberately omitted so two clean builds can be compared.
    """
    with _readonly_connection(path) as conn:
        payload: dict[str, list[tuple]] = {}

        def add_rows(name: str, query: str) -> None:
            """Add a current semantic query when its tables/columns are available.

            A legacy source can be compared at a coarse level, so unsupported
            queries are omitted rather than treating absent columns as data.
            """
            try:
                payload[name] = [tuple(row) for row in conn.execute(query)]
            except sqlite3.OperationalError:
                return

        if {"source_file_id", "active_ingestion_run_id", "sha256", "parse_status"}.issubset(
            _columns(conn, "source_files")
        ) and {
            "ingestion_run_id",
            "parser_name",
            "parser_version",
            "contract_version",
            "schema_version",
            "resolver_version",
            "content_hash",
        }.issubset(_columns(conn, "ingestion_runs")):
            add_rows(
                "active_sources",
                """
                SELECT sf.relpath, sf.sha256, sf.parse_status, ir.parser_name, ir.parser_version,
                       ir.contract_version, ir.schema_version, ir.resolver_version, ir.content_hash
                  FROM source_files sf
                  LEFT JOIN ingestion_runs ir ON ir.ingestion_run_id = sf.active_ingestion_run_id
                 ORDER BY sf.relpath, sf.sha256, sf.parse_status, ir.content_hash
                """,
            )

        queries = {
            "accounts": """
                SELECT institution.code, account.account_number, account.account_type,
                       account.nickname, account.base_currency, account.opened_on,
                       account.closed_on, account.notes
                  FROM accounts account
                  JOIN institutions institution ON institution.institution_id = account.institution_id
                 ORDER BY institution.code, account.account_number
            """,
            "account_links": """
                SELECT source_institution.code, source_account.account_number,
                       target_institution.code, target_account.account_number,
                       link.transfer_date, link.notes
                  FROM account_links link
                  JOIN accounts source_account ON source_account.account_id = link.from_account_id
                  JOIN institutions source_institution
                    ON source_institution.institution_id = source_account.institution_id
                  JOIN accounts target_account ON target_account.account_id = link.to_account_id
                  JOIN institutions target_institution
                    ON target_institution.institution_id = target_account.institution_id
                 ORDER BY source_institution.code, source_account.account_number,
                          target_institution.code, target_account.account_number,
                          link.transfer_date, link.notes
            """,
            "instruments": """
                SELECT instrument_key, asset_type, symbol, exchange, currency, name,
                       cusip, isin, option_root, option_expiry, option_strike, option_type,
                       option_multiplier, resolution_method, resolution_confidence
                  FROM instruments
                 ORDER BY instrument_key
            """,
            "securities": """
                SELECT security.security_key, issuer.issuer_key,
                       security.canonical_name, security.asset_type,
                       security.cusip, security.isin, security.journalable
                  FROM securities security
                  LEFT JOIN security_issuers issuer
                    ON issuer.issuer_id = security.issuer_id
                 ORDER BY security.security_key
            """,
            "instrument_market_symbols": """
                SELECT instrument.instrument_key, market.provider,
                       market.provider_symbol, market.status
                  FROM instrument_market_symbols market
                  JOIN instruments instrument
                    ON instrument.instrument_id = market.instrument_id
                 ORDER BY instrument.instrument_key, market.provider
            """,
            "instrument_journal_pairs": """
                SELECT source.instrument_key, target.instrument_key,
                       pair.conversion_ratio, pair.status,
                       pair.effective_from, pair.effective_to
                  FROM instrument_journal_pairs pair
                  JOIN instruments source
                    ON source.instrument_id = pair.from_instrument_id
                  JOIN instruments target
                    ON target.instrument_id = pair.to_instrument_id
                 ORDER BY source.instrument_key, target.instrument_key,
                          pair.effective_from
            """,
            "instrument_resolution_candidates": """
                SELECT institution.code, candidate.normalized_text,
                       candidate.asset_type, candidate.currency,
                       candidate.status, instrument.instrument_key,
                       candidate.resolution_method,
                       candidate.resolution_confidence
                  FROM instrument_resolution_candidates candidate
                  JOIN institutions institution
                    ON institution.institution_id = candidate.institution_id
                  LEFT JOIN instruments instrument
                    ON instrument.instrument_id = candidate.resolved_instrument_id
                 ORDER BY institution.code, candidate.normalized_text,
                          candidate.asset_type, candidate.currency
            """,
            "aliases": """
                SELECT alias.alias, COALESCE(institution.code, ''), instrument.instrument_key
                  FROM instrument_aliases alias
                  JOIN instruments instrument ON instrument.instrument_id = alias.instrument_id
                  LEFT JOIN institutions institution ON institution.institution_id = alias.institution_id
                 ORDER BY alias.alias, COALESCE(institution.code, ''), instrument.instrument_key
            """,
            "identifier_lookups": """
                SELECT identifier_type, asset_type, institution_code, normalized_name,
                       display_name, currency, status, resolved_symbol, resolved_exchange,
                       resolved_name, evidence_url, sample_description, notes
                  FROM instrument_identifier_lookups
                 ORDER BY identifier_type, asset_type, institution_code, normalized_name, currency
            """,
            "statements": """
                SELECT source.relpath, source.sha256, statement.statement_key,
                       institution.code, account.account_number, statement.period_start,
                       statement.period_end, statement.statement_type
                  FROM statements statement
                  JOIN source_files source ON source.source_file_id = statement.source_file_id
                  JOIN accounts account ON account.account_id = statement.account_id
                  JOIN institutions institution ON institution.institution_id = account.institution_id
                 ORDER BY source.relpath, statement.statement_key
            """,
            "snapshot_sets": """
                SELECT statement.statement_key, snapshot.as_of_date, snapshot.currency,
                       snapshot.section_type, snapshot.scope_key, snapshot.completeness,
                       snapshot.opening_total, snapshot.reported_change,
                       snapshot.reported_total, snapshot.validation_status,
                       evidence.evidence_key
                  FROM snapshot_sets snapshot
                  JOIN statements statement ON statement.statement_id = snapshot.statement_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = snapshot.evidence_id
                 ORDER BY statement.statement_key, snapshot.currency, snapshot.section_type, snapshot.scope_key
            """,
            "transactions": """
                SELECT source.relpath, statement.statement_key, institution.code, account.account_number,
                       evidence.evidence_key, instrument.instrument_key, counterpart_institution.code,
                       counterpart_account.account_number, txn.trade_date, txn.settle_date,
                       txn.txn_type, txn.quantity, txn.position_delta,
                       txn.price, txn.gross_amount, txn.commission,
                       txn.other_fees, txn.net_amount, txn.cash_delta,
                       txn.cash_effective_date, txn.currency, txn.tax_country,
                       txn.tax_rate, txn.resolution_method,
                       txn.resolution_confidence, resolution_evidence.evidence_key
                  FROM transactions txn
                  LEFT JOIN source_files source ON source.source_file_id = txn.source_file_id
                  LEFT JOIN statements statement ON statement.statement_id = txn.statement_id
                  JOIN accounts account ON account.account_id = txn.account_id
                  JOIN institutions institution ON institution.institution_id = account.institution_id
                  LEFT JOIN instruments instrument ON instrument.instrument_id = txn.instrument_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = txn.evidence_id
                  LEFT JOIN accounts counterpart_account
                    ON counterpart_account.account_id = txn.counterpart_account_id
                  LEFT JOIN institutions counterpart_institution
                    ON counterpart_institution.institution_id = counterpart_account.institution_id
                  LEFT JOIN source_evidence resolution_evidence
                    ON resolution_evidence.evidence_id = txn.resolution_evidence_id
                 ORDER BY source.relpath, statement.statement_key, evidence.evidence_key,
                          institution.code, account.account_number, txn.trade_date,
                          txn.txn_type, instrument.instrument_key
            """,
            "ticker_changes": """
                SELECT old.instrument_key, new.instrument_key,
                       change.effective_date, change.conversion_ratio,
                       change.status, change.resolution_method,
                       change.resolution_confidence, change.notes
                  FROM instrument_ticker_changes change
                  JOIN instruments old ON old.instrument_id = change.from_instrument_id
                  JOIN instruments new ON new.instrument_id = change.to_instrument_id
                 ORDER BY old.instrument_key, change.effective_date, new.instrument_key
            """,
            "ticker_change_sources": """
                SELECT old.instrument_key, new.instrument_key,
                       change.effective_date, statement.statement_key,
                       evidence.evidence_key
                  FROM instrument_ticker_change_sources source
                  JOIN instrument_ticker_changes change
                    ON change.ticker_change_id = source.ticker_change_id
                  JOIN instruments old ON old.instrument_id = change.from_instrument_id
                  JOIN instruments new ON new.instrument_id = change.to_instrument_id
                  JOIN transactions txn ON txn.transaction_id = source.transaction_id
                  LEFT JOIN statements statement ON statement.statement_id = txn.statement_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = source.evidence_id
                 ORDER BY old.instrument_key, change.effective_date,
                          new.instrument_key, statement.statement_key, evidence.evidence_key
            """,
            "quarantine": """
                SELECT source.relpath, statement.statement_key, institution.code, account.account_number,
                       evidence.evidence_key, quarantine.occurrence, quarantine.reason
                  FROM quarantine_transactions quarantine
                  LEFT JOIN source_files source ON source.source_file_id = quarantine.source_file_id
                  LEFT JOIN statements statement ON statement.statement_id = quarantine.statement_id
                  LEFT JOIN accounts account ON account.account_id = quarantine.account_id
                  LEFT JOIN institutions institution ON institution.institution_id = account.institution_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = quarantine.evidence_id
                 ORDER BY source.relpath, statement.statement_key, evidence.evidence_key,
                          quarantine.occurrence, quarantine.reason
            """,
            "positions": """
                SELECT statement.statement_key, scope.currency, scope.section_type, scope.scope_key,
                       instrument.instrument_key, position.as_of_date, position.quantity,
                       position.avg_cost, position.book_value, position.market_price,
                       position.market_value, position.unrealized_pnl, position.currency,
                       evidence.evidence_key
                  FROM position_snapshots position
                  JOIN statements statement ON statement.statement_id = position.statement_id
                  JOIN snapshot_sets scope ON scope.snapshot_set_id = position.snapshot_set_id
                  JOIN instruments instrument ON instrument.instrument_id = position.instrument_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = position.evidence_id
                 ORDER BY statement.statement_key, scope.currency, scope.section_type,
                          scope.scope_key, instrument.instrument_key
            """,
            "cash": """
                SELECT statement.statement_key, scope.currency, scope.section_type, scope.scope_key,
                       cash.as_of_date, cash.currency, cash.opening_balance, cash.closing_balance,
                       evidence.evidence_key
                  FROM cash_balances cash
                  JOIN statements statement ON statement.statement_id = cash.statement_id
                  JOIN snapshot_sets scope ON scope.snapshot_set_id = cash.snapshot_set_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = cash.evidence_id
                 ORDER BY statement.statement_key, scope.currency, scope.section_type, scope.scope_key
            """,
            "annual_performance": """
                SELECT statement.statement_key, report.currency, report.period_start, report.period_end,
                       report.since_date, report.beginning_market_value,
                       report.deposits_transfers_in, report.withdrawals_transfers_out,
                       report.net_investment_return, report.ending_market_value,
                       report.money_weighted_1y, report.money_weighted_3y,
                       report.money_weighted_5y, report.money_weighted_10y,
                       report.money_weighted_since
                  FROM annual_performance_reports report
                  JOIN statements statement ON statement.statement_id = report.statement_id
                 ORDER BY statement.statement_key, report.currency
            """,
            "position_transaction_links": """
                SELECT statement.statement_key, scope.currency, scope.section_type, scope.scope_key,
                       instrument.instrument_key, evidence.evidence_key, link.quantity_attributed
                  FROM position_transaction_links link
                  JOIN position_snapshots position ON position.snapshot_id = link.snapshot_id
                  JOIN statements statement ON statement.statement_id = position.statement_id
                  JOIN snapshot_sets scope ON scope.snapshot_set_id = position.snapshot_set_id
                  JOIN instruments instrument ON instrument.instrument_id = position.instrument_id
                  JOIN transactions txn ON txn.transaction_id = link.transaction_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = txn.evidence_id
                 ORDER BY statement.statement_key, scope.currency, scope.section_type, scope.scope_key,
                          instrument.instrument_key, evidence.evidence_key
            """,
            "initial_positions": """
                SELECT institution.code, account.account_number, initial.as_of_date,
                       instrument.instrument_key, initial.quantity, initial.avg_cost,
                       initial.currency, initial.notes
                  FROM initial_positions initial
                  JOIN accounts account ON account.account_id = initial.account_id
                  JOIN institutions institution ON institution.institution_id = account.institution_id
                  JOIN instruments instrument ON instrument.instrument_id = initial.instrument_id
                 ORDER BY institution.code, account.account_number, initial.as_of_date,
                          instrument.instrument_key
            """,
            "initial_cash": """
                SELECT institution.code, account.account_number, initial.as_of_date,
                       initial.currency, initial.balance, initial.notes
                  FROM initial_cash initial
                  JOIN accounts account ON account.account_id = initial.account_id
                  JOIN institutions institution ON institution.institution_id = account.institution_id
                 ORDER BY institution.code, account.account_number, initial.as_of_date, initial.currency
            """,
            "reconciliation": """
                SELECT result.reconciliation_key, result.kind, institution.code, account.account_number,
                       statement.statement_key, scope.currency, scope.section_type, scope.scope_key,
                       prior_scope.currency, prior_scope.section_type, prior_scope.scope_key,
                       instrument.instrument_key, result.currency, result.prior_checkpoint,
                       result.current_checkpoint, result.opening_value, result.summed_deltas,
                       result.expected_close, result.reported_close, result.residual,
                       result.tolerance, result.status, result.reason
                  FROM reconciliation_results result
                  JOIN accounts account ON account.account_id = result.account_id
                  JOIN institutions institution ON institution.institution_id = account.institution_id
                  LEFT JOIN statements statement ON statement.statement_id = result.statement_id
                  LEFT JOIN snapshot_sets scope ON scope.snapshot_set_id = result.snapshot_set_id
                  LEFT JOIN snapshot_sets prior_scope
                    ON prior_scope.snapshot_set_id = result.prior_snapshot_set_id
                  LEFT JOIN instruments instrument ON instrument.instrument_id = result.instrument_id
                 ORDER BY result.reconciliation_key
            """,
            "reconciliation_components": """
                SELECT result.reconciliation_key, evidence.evidence_key, component.delta
                  FROM reconciliation_components component
                  JOIN reconciliation_results result
                    ON result.reconciliation_id = component.reconciliation_id
                  JOIN transactions txn ON txn.transaction_id = component.transaction_id
                  LEFT JOIN source_evidence evidence ON evidence.evidence_id = txn.evidence_id
                 ORDER BY result.reconciliation_key, evidence.evidence_key
            """,
        }
        for name, query in queries.items():
            add_rows(name, query)
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _pdf_manifest(statements_dir: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(statements_dir.rglob("*.pdf")):
        digest.update(path.relative_to(statements_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        count += 1
    return {"files": count, "sha256": digest.hexdigest()}


def _redacted_account_ref(institution: str, account_number: str) -> str:
    """Return a stable report-only account reference without exposing its number."""
    payload = f"{institution}\0{account_number}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _coverage_summary(path: Path | str) -> dict[str, object]:
    with _readonly_connection(path) as conn:
        result: dict[str, object] = {"counts": _table_counts(path)}
        if all(_table_exists(conn, table) for table in ("statements", "accounts", "institutions")):
            institution_rows = [
                {"institution": row["institution_code"], "statements": int(row["statements"])}
                for row in conn.execute(
                    """
                    SELECT i.code AS institution_code, COUNT(*) AS statements
                      FROM statements s
                      JOIN accounts a ON a.account_id = s.account_id
                      JOIN institutions i ON i.institution_id = a.institution_id
                     GROUP BY i.code
                     ORDER BY i.code
                    """
                )
            ]
            result["statements_by_institution"] = institution_rows
            statement_rows = conn.execute(
                """
                SELECT s.statement_id, i.code AS institution_code, a.account_number,
                       s.period_start, s.period_end, s.statement_type
                  FROM statements s
                  JOIN accounts a ON a.account_id = s.account_id
                  JOIN institutions i ON i.institution_id = a.institution_id
                 ORDER BY i.code, a.account_number, s.period_start, s.period_end, s.statement_type
                """
            ).fetchall()
            currencies_by_statement: dict[int, set[str]] = {}
            currency_tables = ("snapshot_sets",) if _table_exists(conn, "snapshot_sets") else (
                "position_snapshots",
                "cash_balances",
            )
            for table in currency_tables:
                columns = _columns(conn, table)
                if not {"statement_id", "currency"}.issubset(columns):
                    continue
                for row in conn.execute(f"SELECT statement_id, currency FROM {table}"):
                    currencies_by_statement.setdefault(int(row["statement_id"]), set()).add(
                        str(row["currency"])
                    )
            coverage_rows = [
                (
                    row["institution_code"],
                    row["account_number"],
                    row["period_start"],
                    row["period_end"],
                    row["statement_type"],
                    tuple(sorted(currencies_by_statement.get(int(row["statement_id"]), set()))),
                )
                for row in statement_rows
            ]
            result["statement_coverage"] = [
                {
                    "institution": str(row["institution_code"]),
                    "account_ref": _redacted_account_ref(
                        str(row["institution_code"]), str(row["account_number"])
                    ),
                    "period_start": str(row["period_start"]),
                    "period_end": str(row["period_end"]),
                    "statement_type": str(row["statement_type"]),
                    "currencies": sorted(currencies_by_statement.get(int(row["statement_id"]), set())),
                }
                for row in statement_rows
            ]
            result["statement_coverage_sha256"] = hashlib.sha256(
                json.dumps(coverage_rows, ensure_ascii=True, separators=(",", ":"), default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
        if all(_table_exists(conn, table) for table in ("snapshot_sets", "statements", "accounts", "institutions")):
            scope_rows = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT i.code, a.account_number, s.period_end, ss.currency,
                           ss.section_type, ss.scope_key, ss.completeness
                      FROM snapshot_sets ss
                      JOIN statements s ON s.statement_id = ss.statement_id
                      JOIN accounts a ON a.account_id = ss.account_id
                      JOIN institutions i ON i.institution_id = a.institution_id
                     ORDER BY i.code, a.account_number, s.period_end, ss.currency,
                              ss.section_type, ss.scope_key
                    """
                )
            ]
            result["scope_coverage"] = [
                {
                    "institution": str(row[0]),
                    "account_ref": _redacted_account_ref(str(row[0]), str(row[1])),
                    "period_end": str(row[2]),
                    "currency": str(row[3]),
                    "section_type": str(row[4]),
                    "scope_key": str(row[5]),
                    "completeness": str(row[6]),
                }
                for row in scope_rows
            ]
            result["scope_coverage_sha256"] = hashlib.sha256(
                json.dumps(scope_rows, ensure_ascii=True, separators=(",", ":"), default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
        if _table_exists(conn, "reconciliation_results"):
            result["reconciliation_statuses"] = [
                {"kind": row["kind"], "status": row["status"], "count": int(row["count"])}
                for row in conn.execute(
                    """
                    SELECT kind, status, COUNT(*) AS count
                      FROM reconciliation_results
                     GROUP BY kind, status
                     ORDER BY kind, status
                    """
                )
            ]
            residual_rows = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT kind, status, residual, tolerance
                      FROM reconciliation_results
                     WHERE residual IS NOT NULL
                     ORDER BY kind, status, residual, tolerance
                    """
                )
            ]
            result["residual_fingerprint"] = {
                "rows": len(residual_rows),
                "sha256": hashlib.sha256(
                    json.dumps(residual_rows, ensure_ascii=True, separators=(",", ":"), default=str).encode(
                        "utf-8"
                    )
                ).hexdigest(),
            }
        if _table_exists(conn, "instruments"):
            columns = _columns(conn, "instruments")
            if "resolution_method" in columns:
                result["unresolved_identities"] = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM instruments WHERE resolution_method = 'unresolved_printed_identity'"
                    ).fetchone()["n"]
                )
    return result


def _institution_statement_count(summary: dict[str, object], institution: str) -> int:
    rows = summary.get("statements_by_institution", [])
    if not isinstance(rows, list):
        return 0
    for row in rows:
        if isinstance(row, dict) and row.get("institution") == institution:
            return int(row.get("statements", 0))
    return 0


def _totals_fingerprint(path: Path | str) -> dict[str, object]:
    """Compare reported totals without placing private values in the report."""
    with _readonly_connection(path) as conn:
        payload: dict[str, list[tuple]] = {}
        if {"currency", "as_of_date", "market_value"}.issubset(_columns(conn, "position_snapshots")):
            payload["positions"] = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT currency, as_of_date, COUNT(*) AS rows, SUM(COALESCE(market_value, 0)) AS value
                      FROM position_snapshots
                     GROUP BY currency, as_of_date
                     ORDER BY currency, as_of_date
                    """
                )
            ]
        if {"currency", "as_of_date", "closing_balance"}.issubset(_columns(conn, "cash_balances")):
            payload["cash"] = [
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT currency, as_of_date, COUNT(*) AS rows, SUM(closing_balance) AS value
                      FROM cash_balances
                     GROUP BY currency, as_of_date
                     ORDER BY currency, as_of_date
                    """
                )
            ]
    return {
        "available": bool(payload),
        "sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        ).hexdigest(),
        "position_currency_dates": len(payload.get("positions", [])),
        "cash_currency_dates": len(payload.get("cash", [])),
    }


def _default_rebuild_runner(
    target_db: Path,
    statements_dir: Path,
    repo_root: Path,
    log_dir: Path,
) -> dict[str, object]:
    logger = logging.getLogger(f"ledger.shadow.ingest.{uuid.uuid4().hex}")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return run_ingest(
        path=target_db,
        statements_dir=statements_dir,
        repo_root=repo_root,
        log_dir=log_dir,
        force=True,
        logger=logger,
    )


def _build_one(
    *,
    stage_db: Path,
    state: CuratedState,
    statements_dir: Path,
    repo_root: Path,
    log_dir: Path,
    rebuild_runner: RebuildRunner,
) -> dict[str, object]:
    sqlite_db.init_db(stage_db)
    imported_identity = import_identity_state(stage_db, state)
    ingest_summary = rebuild_runner(stage_db, statements_dir, repo_root, log_dir) or {}
    imported_initials = import_manual_initials(stage_db, state)
    inferred_initials = infer_initials(stage_db)
    # Rebuild generated equations after all trusted anchors exist. This leaves
    # non-generated reviewed annotations untouched.
    reconciliation = reconcile_after_ingest(stage_db)
    imported_annotations = import_reviewed_annotations(stage_db, state)
    return {
        "identity": imported_identity,
        "ingest": ingest_summary,
        "manual_initials": imported_initials,
        "inferred_initials": inferred_initials,
        "reconciliation": reconciliation,
        "annotations": imported_annotations,
        "content_hash": _content_hash(stage_db),
    }


def _stage_path(target_db: Path, label: str) -> Path:
    return target_db.with_name(f".{target_db.stem}.{label}.{uuid.uuid4().hex}{target_db.suffix}")


def _companion_config_path(target_db: Path) -> Path:
    return target_db.with_name(f"{target_db.stem}.config.json")


def _report_path(target_db: Path) -> Path:
    return target_db.with_name(f"{target_db.stem}.report.json")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    staged.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, path)


def build_shadow(
    *,
    source_db: Path | str = config.SQLITE_PATH,
    target_db: Path | str | None = None,
    statements_dir: Path | None = None,
    report_path: Path | None = None,
    repo_root: Path | None = None,
    replace: bool = False,
    verify_reproducible: bool = True,
    rebuild_runner: RebuildRunner | None = None,
) -> dict:
    """Build a fresh shadow ledger and a redacted, deterministic comparison.

    The source database is read only. The target is not replaced until every
    requested rebuild succeeds and, when enabled, the two clean build hashes
    match. This function never switches the live database path.
    """
    source = Path(source_db).resolve(strict=True)
    target = Path(target_db or (config.DATA_DIR / "ledger.vnext.sqlite")).resolve()
    inputs = (statements_dir or config.STATEMENTS_DIR).resolve(strict=True)
    root = (repo_root or config.ROOT).resolve()
    if not inputs.is_dir():
        raise ValueError(f"statements directory is not a directory: {inputs}")
    if source == target:
        raise ValueError("source_db and target_db must be different files")
    if target.exists() and not replace:
        raise FileExistsError(f"shadow target already exists: {target}; pass replace=True to retain it as a backup")

    state = export_curated_state(source)
    before_manifest = _pdf_manifest(inputs)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_one = _stage_path(target, "build")
    stage_two: Path | None = None
    log_root = target.parent / f"{target.stem}.logs"
    runner = rebuild_runner or _default_rebuild_runner
    first = _build_one(
        stage_db=stage_one,
        state=state,
        statements_dir=inputs,
        repo_root=root,
        log_dir=log_root / "first",
        rebuild_runner=runner,
    )
    _checkpoint_stopped_database(stage_one)
    first["content_hash"] = _content_hash(stage_one)
    reproducibility: dict[str, object] = {
        "requested": verify_reproducible,
        "status": "not_requested",
        "first_content_hash": first["content_hash"],
        "second_content_hash": None,
    }
    if verify_reproducible:
        stage_two = _stage_path(target, "verify")
        second = _build_one(
            stage_db=stage_two,
            state=state,
            statements_dir=inputs,
            repo_root=root,
            log_dir=log_root / "second",
            rebuild_runner=runner,
        )
        _checkpoint_stopped_database(stage_two)
        second["content_hash"] = _content_hash(stage_two)
        reproducibility["second_content_hash"] = second["content_hash"]
        reproducibility["status"] = (
            "passed" if first["content_hash"] == second["content_hash"] else "failed"
        )
        if reproducibility["status"] != "passed":
            raise RuntimeError("shadow rebuild is not reproducible; staged databases were retained for review")

    after_manifest = _pdf_manifest(inputs)
    if before_manifest != after_manifest:
        raise RuntimeError("statement PDF manifest changed during the shadow rebuild")

    previous_target: Path | None = None
    previous_config: Path | None = None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if target.exists():
        # A prior shadow is still derived data, but it must be a closed,
        # complete SQLite file before it is retained under its backup name.
        _checkpoint_stopped_database(target)
        previous_target = target.with_name(f"{target.stem}.previous-{timestamp}{target.suffix}")
        os.replace(target, previous_target)
    os.replace(stage_one, target)
    if stage_two is not None and stage_two.exists():
        stage_two.unlink()

    companion_config = _companion_config_path(target)
    if companion_config.exists():
        previous_config = companion_config.with_name(
            f"{companion_config.stem}.previous-{timestamp}{companion_config.suffix}"
        )
        os.replace(companion_config, previous_config)
    if state.config_bytes is not None:
        staged_config = _stage_path(companion_config, "config")
        staged_config.write_bytes(state.config_bytes)
        os.replace(staged_config, companion_config)

    source_summary = _coverage_summary(source)
    shadow_summary = _coverage_summary(target)
    recovered_segments = {
        institution: {
            "source_statements": _institution_statement_count(source_summary, institution),
            "shadow_statements": _institution_statement_count(shadow_summary, institution),
            "delta": _institution_statement_count(shadow_summary, institution)
            - _institution_statement_count(source_summary, institution),
        }
        for institution in ("RBC_DI", "TD_WB")
    }
    report = {
        "report_version": SHADOW_REPORT_VERSION,
        "source_db_name": source.name,
        "target_db_name": target.name,
        "source_content_hash": _content_hash(source),
        "target_content_hash": _content_hash(target),
        "reproducibility": reproducibility,
        "pdf_manifest": {"before": before_manifest, "after": after_manifest},
        "source": source_summary,
        "shadow": shadow_summary,
        "recovered_rbc_td_segments": recovered_segments,
        "reported_totals": {
            "source": _totals_fingerprint(source),
            "shadow": _totals_fingerprint(target),
            "redacted": True,
        },
        "curated_state": {
            "exported": state.counts(),
            "unavailable": state.unavailable,
            "imported": {
                "identity": first["identity"],
                "manual_initials": first["manual_initials"],
                "annotations": first["annotations"],
            },
            "config_sha256": state.config_sha256,
            "companion_config_name": companion_config.name if state.config_bytes is not None else None,
        },
        "previous_shadow_name": previous_target.name if previous_target else None,
        "previous_shadow_config_name": previous_config.name if previous_config else None,
        "manual_review": {
            "status": "pending",
            "required": [
                "spot-check parser layouts and previously colliding RBC/TD sources against PDFs",
                "review latest account/currency statements and largest remaining residuals",
                "confirm every unmatched curated item is intentionally accounted for",
                "confirm backend is stopped before any separate cutover command",
            ],
            "note": "The report deliberately stores totals and identifiers as fingerprints/counts, not statement values.",
        },
    }
    output = report_path or _report_path(target)
    _write_json(output, report)
    return {**report, "report_path": str(output)}


def sign_off_report(
    report_path: Path | str,
    *,
    reviewer: str,
    confirmation: str,
    acknowledge_unmapped: bool = False,
) -> dict:
    """Record a human review sign-off; this does not perform a cutover."""
    path = Path(report_path).resolve(strict=True)
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("reproducibility", {}).get("status") != "passed":
        raise ValueError("a reproducible rebuild is required before sign-off")
    curated = report.get("curated_state", {}).get("exported", {})
    unmapped = int(curated.get("unmapped_annotations", 0)) + int(
        curated.get("unmapped_portfolio_account_ids", 0)
    )
    if unmapped and not acknowledge_unmapped:
        raise ValueError("unmapped curated state requires explicit acknowledgement")
    review = report.setdefault("manual_review", {})
    review.update(
        {
            "status": "signed_off",
            "reviewer": reviewer,
            "confirmation": confirmation,
            "acknowledged_unmapped": acknowledge_unmapped,
            "signed_at": datetime.now(UTC).isoformat(),
        }
    )
    _write_json(path, report)
    return report


def _checkpoint_stopped_database(path: Path) -> None:
    """Flush a stopped SQLite WAL before an explicitly requested file swap."""
    conn = sqlite3.connect(path)
    try:
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    if checkpoint is not None and int(checkpoint[0]) != 0:
        raise RuntimeError(f"SQLite checkpoint is busy: {path.name}")
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def cutover_shadow(
    *,
    source_db: Path | str,
    shadow_db: Path | str,
    report_path: Path | str,
    backend_stopped: bool,
    confirm_live_db: str,
) -> dict[str, str]:
    """Atomically replace a stopped live DB after a human-signed report.

    This is intentionally separate from :func:`build_shadow`; callers must
    explicitly acknowledge a stopped backend and the live filename.
    """
    source = Path(source_db).resolve(strict=True)
    shadow = Path(shadow_db).resolve(strict=True)
    report = json.loads(Path(report_path).resolve(strict=True).read_text(encoding="utf-8"))
    if report.get("manual_review", {}).get("status") != "signed_off":
        raise ValueError("a signed-off shadow report is required before cutover")
    if not backend_stopped:
        raise ValueError("backend_stopped=True is required before cutover")
    if confirm_live_db != source.name:
        raise ValueError("confirm_live_db must exactly match the live database filename")
    if source.parent != shadow.parent:
        raise ValueError("source and shadow must share a directory for atomic replacement")
    _checkpoint_stopped_database(source)
    _checkpoint_stopped_database(shadow)
    for database in (source, shadow):
        for suffix in ("-wal", "-shm"):
            if database.with_name(database.name + suffix).exists():
                raise RuntimeError(f"refusing cutover while SQLite sidecar exists: {database.name}{suffix}")
    if _content_hash(shadow) != report.get("target_content_hash"):
        raise ValueError("shadow database no longer matches the signed-off report")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = source.with_name(f"{source.stem}.backup-{stamp}{source.suffix}")
    shutil.copy2(source, backup)
    if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(backup.read_bytes()).digest():
        raise RuntimeError("backup verification failed; live database was not switched")
    os.replace(shadow, source)
    return {"live_db": str(source), "backup_db": str(backup)}


def rollback_shadow(
    *,
    live_db: Path | str,
    backup_db: Path | str,
    backend_stopped: bool,
    confirm_live_db: str,
) -> dict[str, str]:
    """Restore a retained backup without deleting the backup itself."""
    live = Path(live_db).resolve(strict=True)
    backup = Path(backup_db).resolve(strict=True)
    if not backend_stopped:
        raise ValueError("backend_stopped=True is required before rollback")
    if confirm_live_db != live.name:
        raise ValueError("confirm_live_db must exactly match the live database filename")
    if live.parent != backup.parent:
        raise ValueError("live and backup must share a directory for atomic replacement")
    _checkpoint_stopped_database(live)
    for suffix in ("-wal", "-shm"):
        if live.with_name(live.name + suffix).exists():
            raise RuntimeError(f"refusing rollback while SQLite sidecar exists: {live.name}{suffix}")
    staged = _stage_path(live, "rollback")
    shutil.copy2(backup, staged)
    os.replace(staged, live)
    return {"live_db": str(live), "restored_from": str(backup)}

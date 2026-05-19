"""Orchestrate the full ingest pipeline: discover → extract → normalize → store."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import click

from trade_history.db import sqlite as db_sqlite
from trade_history.extractors import registry as _reg_module  # noqa: F401 — triggers imports
from trade_history.extractors.base import UnknownStatementError
from trade_history.extractors.registry import ExtractorRegistry
from trade_history.extractors.utils import cache_docling_json
from trade_history.ingest.docling_linker import link_transactions_to_docling
from trade_history.ingest.normalizer import store_statement
from trade_history.ingest.transfer import match_transfer_pairs

log = logging.getLogger(__name__)

# Skip these directory patterns when discovering PDFs
_SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", ".uv-cache"}


class IngestPipeline:
    def __init__(self, db_path: Path, duckdb_path: Path) -> None:
        self.db_path = db_path
        self.duckdb_path = duckdb_path

    def run(
        self,
        statements_dir: Path,
        force: bool = False,
        dry_run: bool = False,
        target_path: Path | None = None,
    ) -> None:
        conn = db_sqlite.init_db(self.db_path)

        if target_path is not None:
            if target_path.is_file():
                pdf_files = [target_path]
            else:
                pdf_files = sorted(
                    p for p in target_path.rglob("*.pdf")
                    if not any(skip in p.parts for skip in _SKIP_DIRS)
                )
            click.echo(f"Targeting {len(pdf_files)} PDF files in {target_path}")
        else:
            pdf_files = sorted(
                p for p in statements_dir.rglob("*.pdf")
                if not any(skip in p.parts for skip in _SKIP_DIRS)
            )
            click.echo(f"Found {len(pdf_files)} PDF files in {statements_dir}")

        already_processed = self._get_processed_files(conn) if not force else set()

        ok = partial = error = skipped = 0
        balance_ok = balance_mismatch = balance_missing = 0

        for pdf_path in pdf_files:
            rel = str(pdf_path)
            if rel in already_processed:
                skipped += 1
                continue

            try:
                extractor = ExtractorRegistry.resolve(pdf_path)
            except UnknownStatementError as exc:
                log.warning("Skipping unknown format: %s", pdf_path.name)
                if not dry_run:
                    self._quarantine(conn, str(pdf_path), "", str(exc))
                error += 1
                continue

            # Pre-load cached docling JSON from database to skip re-running docling
            if not dry_run:
                cached = self._get_cached_docling(conn, str(pdf_path))
                if cached:
                    cache_docling_json(str(pdf_path), cached)

            try:
                results = list(extractor.extract(pdf_path))
            except Exception as exc:
                log.error("Extraction failed for %s: %s", pdf_path.name, exc)
                if not dry_run:
                    self._quarantine(conn, str(pdf_path), "", str(exc))
                error += 1
                continue

            # Retrieve docling JSON from extractor (stored during extract())
            docling_dict = getattr(extractor, "_docling_dict", None)
            docling_json_str = json.dumps(docling_dict) if docling_dict else None

            # Save docling JSON to data/docling_json/ for offline access
            if docling_dict and not dry_run:
                self._save_docling_json(pdf_path, docling_dict)

            tx_total = 0
            stmt_status = "ok"
            for stmt, transactions, positions in results:
                if dry_run:
                    click.echo(
                        f"  [DRY RUN] {pdf_path.name}: "
                        f"{stmt.institution} {stmt.account_id} "
                        f"-> {len(transactions)} txs, {len(positions)} positions"
                    )
                    continue

                try:
                    # Delete existing transactions for this source file before re-inserting
                    # (handles --force re-ingest without duplicates)
                    conn.execute(
                        "DELETE FROM transactions WHERE source_file = ?", (str(pdf_path),)
                    )

                    # Register statement first to get statement_id
                    stmt_id = self._register_statement(
                        conn, pdf_path, stmt, stmt_status,
                        docling_json=docling_json_str,
                    )

                    count = store_statement(
                        conn, stmt, transactions, positions, pdf_path,
                        statement_id=stmt_id,
                    )
                    tx_total += count

                    # Link transactions to docling elements
                    if docling_dict:
                        link_transactions_to_docling(conn, stmt_id, docling_dict)

                    # Balance validation
                    bal_status = self._validate_balance(
                        conn, stmt, transactions, stmt_id
                    )
                    if bal_status == "ok":
                        balance_ok += 1
                    elif bal_status == "mismatch":
                        balance_mismatch += 1
                        log.warning(
                            "Balance mismatch for %s (%s %s)",
                            pdf_path.name, stmt.institution, stmt.account_id,
                        )
                    else:
                        balance_missing += 1

                except Exception as exc:
                    log.error("Store failed for %s: %s", pdf_path.name, exc)
                    stmt_status = "partial"

            if not dry_run and stmt_status != "ok":
                # Update the statement status if it failed
                self._update_statement_status(conn, str(pdf_path), tx_total, stmt_status)

            if stmt_status == "ok":
                ok += 1
            else:
                partial += 1

            click.echo(
                f"  {'[DRY]' if dry_run else '[OK]'} {pdf_path.name}: {tx_total} transactions"
            )

        if not dry_run:
            pairs = match_transfer_pairs(conn)
            click.echo(f"\nTransfer pairs matched: {pairs}")

            from trade_history.analytics.monthly import compute_monthly_balances
            mb_count = compute_monthly_balances(conn)
            click.echo(f"Monthly balance snapshots: {mb_count}")

        conn.close()
        click.echo(
            f"\nIngest complete — OK: {ok}, Partial: {partial}, "
            f"Error: {error}, Skipped: {skipped}"
        )
        click.echo(
            f"Balance validation — OK: {balance_ok}, Mismatch: {balance_mismatch}, "
            f"Missing: {balance_missing}"
        )

    def _get_processed_files(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT source_file FROM statement_registry WHERE status = 'ok'"
        ).fetchall()
        return {row[0] for row in rows}

    def _get_cached_docling(
        self, conn: sqlite3.Connection, source_file: str
    ) -> dict | None:
        """Return stored docling JSON dict for a source file, or None.

        Checks: 1) database (statement_registry.docling_json),
                2) disk cache (data/docling_json/<parent>/<stem>.json).
        Disk fallback is critical when re-ingesting into a fresh DB.
        """
        row = conn.execute(
            "SELECT docling_json FROM statement_registry WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                pass

        # Disk fallback: data/docling_json/<parent_dir>/<stem>.json
        pdf_path = Path(source_file)
        disk_path = self.db_path.parent / "docling_json" / pdf_path.parent.name / f"{pdf_path.stem}.json"
        if disk_path.exists():
            try:
                cached = json.loads(disk_path.read_text(encoding="utf-8"))
                log.info("Loaded docling cache from disk: %s", disk_path.name)
                return cached
            except (json.JSONDecodeError, OSError):
                pass

        return None

    def _save_docling_json(self, pdf_path: Path, doc_dict: dict) -> None:
        """Write docling JSON to data/docling_json/<parent_dir>/<stem>.json."""
        out_dir = self.db_path.parent / "docling_json" / pdf_path.parent.name
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{pdf_path.stem}.json"
        out_file.write_text(json.dumps(doc_dict, ensure_ascii=False), encoding="utf-8")

    def _quarantine(
        self, conn: sqlite3.Connection, source_file: str, raw_text: str, reason: str
    ) -> None:
        conn.execute(
            "INSERT INTO quarantine_transactions (source_file, raw_text, reason) VALUES (?, ?, ?)",
            (source_file, raw_text, reason),
        )
        conn.commit()

    def _register_statement(
        self,
        conn: sqlite3.Connection,
        pdf_path: Path,
        stmt: object,
        status: str,
        docling_json: str | None = None,
    ) -> int:
        """Register statement and return its id."""
        institution = stmt.institution
        account_id = stmt.account_id
        period_start = stmt.period_start.isoformat() if stmt.period_start else None
        period_end = stmt.period_end.isoformat() if stmt.period_end else None

        # Get balance values from the statement — store per currency
        ob = getattr(stmt, "opening_balance", None)
        cb = getattr(stmt, "closing_balance", None)
        cur_code = getattr(stmt, "primary_currency", "CAD")

        ob_cad = float(ob) if ob is not None and cur_code == "CAD" else None
        ob_usd = float(ob) if ob is not None and cur_code == "USD" else None
        cb_cad = float(cb) if cb is not None and cur_code == "CAD" else None
        cb_usd = float(cb) if cb is not None and cur_code == "USD" else None

        cur = conn.execute(
            """
            INSERT INTO statement_registry
                (source_file, institution, account_id, period_start, period_end,
                 transaction_count, status,
                 opening_balance_cad, opening_balance_usd,
                 closing_balance_cad, closing_balance_usd,
                 docling_json)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                processed_at = datetime('now'),
                institution = excluded.institution,
                account_id = excluded.account_id,
                period_start = excluded.period_start,
                period_end = excluded.period_end,
                status = excluded.status,
                opening_balance_cad = COALESCE(excluded.opening_balance_cad, statement_registry.opening_balance_cad),
                opening_balance_usd = COALESCE(excluded.opening_balance_usd, statement_registry.opening_balance_usd),
                closing_balance_cad = COALESCE(excluded.closing_balance_cad, statement_registry.closing_balance_cad),
                closing_balance_usd = COALESCE(excluded.closing_balance_usd, statement_registry.closing_balance_usd),
                docling_json = COALESCE(excluded.docling_json, statement_registry.docling_json)
            RETURNING id
            """,
            (
                str(pdf_path),
                institution,
                account_id,
                period_start,
                period_end,
                status,
                ob_cad, ob_usd, cb_cad, cb_usd,
                docling_json,
            ),
        )
        row = cur.fetchone()
        if row:
            conn.commit()
            return row[0]

        # Fallback: look up existing
        row = conn.execute(
            "SELECT id FROM statement_registry WHERE source_file = ?", (str(pdf_path),)
        ).fetchone()
        conn.commit()
        return row[0] if row else 0

    def _update_statement_status(
        self, conn: sqlite3.Connection, source_file: str, tx_count: int, status: str
    ) -> None:
        conn.execute(
            """UPDATE statement_registry SET
                transaction_count = ?, status = ?, processed_at = datetime('now')
               WHERE source_file = ?""",
            (tx_count, status, source_file),
        )
        conn.commit()

    def _validate_balance(
        self,
        conn: sqlite3.Connection,
        stmt: object,
        transactions: list,
        stmt_id: int,
    ) -> str:
        """Validate opening_balance + transactions = closing_balance."""
        ob = getattr(stmt, "opening_balance", None)
        cb = getattr(stmt, "closing_balance", None)

        if ob is None or cb is None:
            conn.execute(
                "UPDATE statement_registry SET balance_validated = 'missing', transaction_count = ? WHERE id = ?",
                (len(transactions), stmt_id),
            )
            conn.commit()
            return "missing"

        tx_sum = sum(tx.amount for tx in transactions)
        expected_cb = ob + tx_sum
        diff = abs(float(expected_cb) - float(cb))

        if diff <= 1.0:  # Allow $1 rounding tolerance
            status = "ok"
        else:
            status = "mismatch"
            log.warning(
                "Balance mismatch: opening=%s + txs=%s = %s, expected closing=%s (diff=$%.2f)",
                ob, tx_sum, expected_cb, cb, diff,
            )

        conn.execute(
            "UPDATE statement_registry SET balance_validated = ?, transaction_count = ? WHERE id = ?",
            (status, len(transactions), stmt_id),
        )
        conn.commit()
        return status

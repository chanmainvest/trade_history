"""Ingest pipeline scaffolding. Real parsers slot in via the registry."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .. import config
from ..db import sqlite as sqlite_db
from ..logging_setup import get_logger, jsonl_path
from ..parsers import registry  # noqa: F401  (ensures parsers register)
from ..parsers.registry import select_parser
from ..parsers.types import ParsedStatement, ParseResult
from ..pdf_text import PdfText, extract_pdf

log = get_logger("ingest")

SKIPPABLE_PARSE_STATUSES = {"ok", "partial", "skipped"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unchanged_source_file_id(conn, *, relpath: str, sha256: str) -> int | None:
    row = conn.execute(
        "SELECT source_file_id, parse_status FROM source_files WHERE relpath = ? AND sha256 = ?",
        (relpath, sha256),
    ).fetchone()
    if row and row["parse_status"] in SKIPPABLE_PARSE_STATUSES:
        return int(row["source_file_id"])
    return None


def _record_source_file(conn, pdf: PdfText, *, parser_name: str | None,
                        parser_version: str | None, parse_status: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO source_files
            (relpath, sha256, size_bytes, page_count, is_image_only,
             parser_name, parser_version, parsed_at, parse_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relpath) DO UPDATE SET
            sha256        = excluded.sha256,
            size_bytes    = excluded.size_bytes,
            page_count    = excluded.page_count,
            is_image_only = excluded.is_image_only,
            parser_name   = excluded.parser_name,
            parser_version= excluded.parser_version,
            parsed_at     = excluded.parsed_at,
            parse_status  = excluded.parse_status
        RETURNING source_file_id
        """,
        (
            pdf.relpath, pdf.sha256, pdf.size_bytes, pdf.page_count,
            int(pdf.is_image_only), parser_name, parser_version,
            datetime.utcnow().isoformat(timespec="seconds"), parse_status,
        ),
    )
    return cur.fetchone()[0]


def _write_statement(conn, *, source_file_id: int, institution_code: str,
                     stmt: ParsedStatement) -> None:
    inst_id = sqlite_db.upsert_institution(conn, institution_code, institution_code)
    acct_id = sqlite_db.upsert_account(
        conn,
        institution_id=inst_id,
        account_number=stmt.account.account_number,
        account_type=stmt.account.account_type,
        base_currency=stmt.account.base_currency,
    )
    cur = conn.execute(
        """
        INSERT INTO statements (source_file_id, account_id, period_start, period_end, statement_type)
        VALUES (?,?,?,?,?)
        ON CONFLICT(source_file_id, account_id, period_end) DO UPDATE SET
            period_start = excluded.period_start,
            statement_type = excluded.statement_type
        RETURNING statement_id
        """,
        (source_file_id, acct_id, stmt.period_start, stmt.period_end, stmt.statement_type),
    )
    statement_id = cur.fetchone()[0]

    # Replace child rows (idempotent re-ingest).
    conn.execute("DELETE FROM transactions WHERE statement_id = ?", (statement_id,))
    conn.execute("DELETE FROM position_snapshots WHERE statement_id = ?", (statement_id,))
    conn.execute("DELETE FROM cash_balances WHERE statement_id = ?", (statement_id,))
    conn.execute("DELETE FROM annual_performance_reports WHERE statement_id = ?", (statement_id,))
    conn.execute(
        "DELETE FROM quarantine_transactions WHERE source_file_id = ? AND account_id = ?",
        (source_file_id, acct_id),
    )

    for t in stmt.transactions:
        instr_id = None
        if t.instrument is not None:
            i = t.instrument
            instr_id = sqlite_db.upsert_instrument(
                conn,
                asset_type=i.asset_type, symbol=i.symbol, currency=i.currency,
                exchange=i.exchange, name=i.name,
                option_root=i.option_root, option_expiry=i.option_expiry,
                option_strike=i.option_strike, option_type=i.option_type,
                option_multiplier=i.option_multiplier,
            )
        conn.execute(
            """INSERT INTO transactions
            (account_id, statement_id, source_file_id, trade_date, settle_date,
             txn_type, instrument_id, quantity, price, gross_amount, commission,
             other_fees, net_amount, currency, tax_country, tax_rate,
             description, raw_line, parser_confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                acct_id, statement_id, source_file_id, t.trade_date, t.settle_date,
                t.txn_type, instr_id, t.quantity, t.price, t.gross_amount,
                t.commission, t.other_fees, t.net_amount, t.currency,
                t.tax_country, t.tax_rate, t.description, t.raw_line,
                t.parser_confidence,
            ),
        )

    for p in stmt.positions:
        i = p.instrument
        instr_id = sqlite_db.upsert_instrument(
            conn,
            asset_type=i.asset_type, symbol=i.symbol, currency=i.currency,
            exchange=i.exchange, name=i.name,
            option_root=i.option_root, option_expiry=i.option_expiry,
            option_strike=i.option_strike, option_type=i.option_type,
            option_multiplier=i.option_multiplier,
        )
        conn.execute(
            """INSERT INTO position_snapshots
            (statement_id, account_id, as_of_date, instrument_id, quantity,
             avg_cost, book_value, market_price, market_value, unrealized_pnl,
             currency, raw_line) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(statement_id, instrument_id) DO UPDATE SET
                quantity      = excluded.quantity,
                avg_cost      = excluded.avg_cost,
                book_value    = excluded.book_value,
                market_price  = excluded.market_price,
                market_value  = excluded.market_value,
                unrealized_pnl= excluded.unrealized_pnl""",
            (
                statement_id, acct_id, stmt.period_end, instr_id, p.quantity,
                p.avg_cost, p.book_value, p.market_price, p.market_value,
                p.unrealized_pnl, p.currency, p.raw_line,
            ),
        )

    for c in stmt.cash_balances:
        conn.execute(
            """INSERT INTO cash_balances
            (statement_id, account_id, as_of_date, currency, opening_balance, closing_balance)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(statement_id, currency) DO UPDATE SET
                opening_balance = excluded.opening_balance,
                closing_balance = excluded.closing_balance""",
            (statement_id, acct_id, stmt.period_end, c.currency,
             c.opening_balance, c.closing_balance),
        )

    for perf in stmt.annual_performance:
        conn.execute(
            """INSERT INTO annual_performance_reports
            (statement_id, account_id, currency, period_start, period_end, since_date,
             beginning_market_value, deposits_transfers_in, withdrawals_transfers_out,
             net_investment_return, ending_market_value, money_weighted_1y,
             money_weighted_3y, money_weighted_5y, money_weighted_10y,
             money_weighted_since)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(statement_id, currency) DO UPDATE SET
                period_start = excluded.period_start,
                period_end = excluded.period_end,
                since_date = excluded.since_date,
                beginning_market_value = excluded.beginning_market_value,
                deposits_transfers_in = excluded.deposits_transfers_in,
                withdrawals_transfers_out = excluded.withdrawals_transfers_out,
                net_investment_return = excluded.net_investment_return,
                ending_market_value = excluded.ending_market_value,
                money_weighted_1y = excluded.money_weighted_1y,
                money_weighted_3y = excluded.money_weighted_3y,
                money_weighted_5y = excluded.money_weighted_5y,
                money_weighted_10y = excluded.money_weighted_10y,
                money_weighted_since = excluded.money_weighted_since""",
            (
                statement_id, acct_id, perf.currency, perf.period_start,
                perf.period_end, perf.since_date, perf.beginning_market_value,
                perf.deposits_transfers_in, perf.withdrawals_transfers_out,
                perf.net_investment_return, perf.ending_market_value,
                perf.money_weighted_1y, perf.money_weighted_3y,
                perf.money_weighted_5y, perf.money_weighted_10y,
                perf.money_weighted_since,
            ),
        )

    for raw, reason in stmt.quarantine:
        conn.execute(
            "INSERT INTO quarantine_transactions(source_file_id, account_id, raw_line, reason) "
            "VALUES (?,?,?,?)",
            (source_file_id, acct_id, raw, reason),
        )


def run_ingest(*, institution: str | None = None, limit: int | None = None, force: bool = False) -> None:
    sqlite_db.init_db()
    seen = 0
    skipped_log = config.LOG_DIR / "skipped_pdfs.log"
    quarantine_jsonl = jsonl_path("quarantine")

    with skipped_log.open("a", encoding="utf-8") as skipf, \
         quarantine_jsonl.open("a", encoding="utf-8") as qf, \
         sqlite_db.session() as conn:

        for folder in sorted(config.STATEMENTS_DIR.iterdir()):
            if not folder.is_dir():
                continue
            if institution and folder.name != institution:
                continue
            inst_code = config.INSTITUTIONS.get(folder.name, folder.name)

            for p in sorted(folder.glob("*.pdf")):
                if limit is not None and seen >= limit:
                    return
                seen += 1
                relpath = p.relative_to(config.ROOT).as_posix()
                if not force:
                    sha = _sha256_file(p)
                    if _unchanged_source_file_id(conn, relpath=relpath, sha256=sha) is not None:
                        log.info("Skipping unchanged %s/%s", folder.name, p.name)
                        continue
                log.info("Reading %s/%s", folder.name, p.name)
                try:
                    pdf = extract_pdf(p, repo_root=config.ROOT)
                except Exception as e:
                    log.exception("extract failed: %s -> %s", p, e)
                    continue

                if pdf.is_image_only:
                    skipf.write(f"{pdf.relpath}\n")
                    _record_source_file(conn, pdf, parser_name=None,
                                        parser_version=None, parse_status="skipped")
                    continue

                parser = select_parser(folder.name, pdf)
                if parser is None:
                    log.warning("no parser claimed %s", pdf.relpath)
                    _record_source_file(conn, pdf, parser_name=None,
                                        parser_version=None, parse_status="failed")
                    continue

                try:
                    result: ParseResult = parser.parse(pdf)
                except Exception as e:
                    log.exception("parser %s crashed on %s: %s", parser.NAME, p, e)
                    _record_source_file(conn, pdf, parser_name=parser.NAME,
                                        parser_version=parser.VERSION,
                                        parse_status="failed")
                    continue

                status = "ok" if result.statements and not result.errors else (
                    "partial" if result.statements else "failed"
                )
                sf_id = _record_source_file(conn, pdf,
                                            parser_name=parser.NAME,
                                            parser_version=parser.VERSION,
                                            parse_status=status)
                for stmt in result.statements:
                    _write_statement(conn, source_file_id=sf_id,
                                     institution_code=inst_code, stmt=stmt)
                    for raw, reason in stmt.quarantine:
                        qf.write(json.dumps({
                            "relpath": pdf.relpath,
                            "account": stmt.account.account_number,
                            "raw": raw, "reason": reason,
                        }) + "\n")
                for err in result.errors:
                    log.warning("%s: %s", pdf.relpath, err)
    from .repair_symbols import repair_symbols

    repair_summary = repair_symbols()
    log.info("Symbol repair after ingest: %s", repair_summary)
    log.info("Ingest finished. %d PDFs scanned.", seen)

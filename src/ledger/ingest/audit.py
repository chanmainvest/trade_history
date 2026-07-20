"""Read-only extraction audit for PDFs or stored text dumps."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from ..logging_setup import get_logger
from ..parsers import registry as _registered_parsers  # noqa: F401
from ..parsers.registry import select_parser
from ..parsers.types import ParsedStatement, ParseResult
from ..parsers.validation import (
    instrument_key,
    statement_key,
    validate_parse_result,
)
from ..pdf_text import PdfText, extract_pdf
from ..quantity import quantity_delta

log = get_logger("extraction_audit")

_SUPPORTED_SUFFIXES = {".pdf", ".txt"}
_NON_CASH_TYPES = {
    "stock_split",
    "stock_split_credit",
    "stock_split_debit",
    "name_change",
    "spinoff",
    "merger",
}


def _normalized_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper()


def _text_dump(path: Path, root: Path) -> PdfText:
    text = path.read_text(encoding="utf-8")
    chunks = text.split("----- PAGE BREAK -----")
    pages: list[str] = []
    for index, chunk in enumerate(chunks):
        lines = chunk.splitlines()
        if index == 0:
            lines = [line for line in lines if not line.startswith("# ")]
        pages.append("\n".join(lines).strip())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        relpath = path.relative_to(root.parent).as_posix()
    except ValueError:
        relpath = path.name
    return PdfText(
        relpath=relpath,
        page_count=len(pages),
        pages=pages,
        sha256=digest,
        size_bytes=path.stat().st_size,
    )


def _load_source(path: Path, root: Path) -> PdfText:
    if path.suffix.lower() == ".txt":
        return _text_dump(path, root)
    return extract_pdf(
        path,
        repo_root=root.parent,
        include_layout=path.parent.name == "RBC Invest Direct",
    )


def _discover(root: Path, institution: str | None, limit: int | None) -> list[Path]:
    if root.is_file():
        candidates = [root] if root.suffix.lower() in _SUPPORTED_SUFFIXES else []
    else:
        candidates = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES
        )
    if institution:
        candidates = [path for path in candidates if path.parent.name == institution]
    return candidates[:limit] if limit is not None else candidates


def _source_coverage(pdf: PdfText, statements: list[ParsedStatement]) -> dict:
    source_lines = [
        _normalized_line(line)
        for page in pdf.pages
        for line in page.splitlines()
        if _normalized_line(line)
    ]
    evidence: list[str] = []
    for statement in statements:
        evidence.extend(
            _normalized_line(transaction.raw_line)
            for transaction in statement.transactions
            if transaction.raw_line.strip()
        )
        evidence.extend(
            _normalized_line(position.raw_line or "")
            for position in statement.positions
            if (position.raw_line or "").strip()
        )
        evidence.extend(
            _normalized_line(raw_line)
            for raw_line, _reason in statement.quarantine
            if raw_line.strip()
        )
    evidence = [value for value in evidence if value]
    matched = sum(
        1
        for line in source_lines
        if any(line == raw or line in raw or (len(raw) > 12 and raw in line) for raw in evidence)
    )
    return {
        "nonempty_source_lines": len(source_lines),
        "matched_source_lines": matched,
        "coverage_ratio": round(matched / len(source_lines), 6) if source_lines else 0.0,
        "evidence_items": len(evidence),
    }


def _cash_checks(statement: ParsedStatement) -> list[dict]:
    checks: list[dict] = []
    for cash in statement.cash_balances:
        deltas = [
            transaction.net_amount
            for transaction in statement.transactions
            if transaction.currency == cash.currency and transaction.net_amount is not None
        ]
        missing = sum(
            1
            for transaction in statement.transactions
            if (
                transaction.currency == cash.currency
                and transaction.net_amount is None
                and transaction.txn_type not in _NON_CASH_TYPES
            )
        )
        calculable = cash.opening_balance is not None and missing == 0
        expected = cash.opening_balance + sum(deltas) if calculable else None
        residual = cash.closing_balance - expected if expected is not None else None
        checks.append(
            {
                "currency": cash.currency,
                "opening": cash.opening_balance,
                "delta_sum": round(sum(deltas), 8),
                "reported_close": cash.closing_balance,
                "expected_close": round(expected, 8) if expected is not None else None,
                "residual": round(residual, 8) if residual is not None else None,
                "missing_cash_delta_rows": missing,
                "status": (
                    "balanced"
                    if residual is not None and abs(residual) <= 0.01
                    else "unbalanced"
                    if residual is not None
                    else "incomplete"
                ),
            }
        )
    return checks


def _identity_quality(statements: list[ParsedStatement]) -> dict:
    instruments = [
        transaction.instrument
        for statement in statements
        for transaction in statement.transactions
        if transaction.instrument is not None
    ] + [
        position.instrument
        for statement in statements
        for position in statement.positions
    ]
    keys = {instrument_key(instrument) for instrument in instruments}
    synthetic = {
        instrument_key(instrument)
        for instrument in instruments
        if (
            "_" in instrument.symbol
            or instrument.symbol.upper().startswith("UNKNOWN")
            or len(instrument.symbol) > 15
        )
    }
    return {
        "instrument_rows": len(instruments),
        "logical_instruments": len(keys),
        "synthetic_or_unresolved": len(synthetic),
    }


def _record_for_result(
    *,
    path: Path,
    institution: str,
    pdf: PdfText,
    result: ParseResult,
) -> dict:
    validation = validate_parse_result(result, page_count=pdf.page_count)
    statements = result.statements
    cash_checks = [
        {
            "statement_index": index,
            "statement_key": list(statement_key(statement)),
            "checks": _cash_checks(statement),
        }
        for index, statement in enumerate(statements)
        if statement.cash_balances
    ]
    return {
        "record_type": "source",
        "path": pdf.relpath,
        "institution_folder": institution,
        "source_kind": path.suffix.lower().lstrip("."),
        "sha256": pdf.sha256,
        "page_count": pdf.page_count,
        "image_only": pdf.is_image_only,
        "status": (
            "skipped"
            if result.status == "skipped"
            else "invalid" if not validation.is_valid else "parsed"
        ),
        "parser": {"name": result.parser_name, "version": result.parser_version},
        "statement_keys": [list(statement_key(statement)) for statement in statements],
        "counts": {
            "statements": len(statements),
            "transactions": sum(len(statement.transactions) for statement in statements),
            "positions": sum(len(statement.positions) for statement in statements),
            "cash_balances": sum(len(statement.cash_balances) for statement in statements),
            "annual_performance": sum(len(statement.annual_performance) for statement in statements),
            "quarantine": sum(len(statement.quarantine) for statement in statements),
        },
        "parser_errors": list(result.errors),
        "skip_reason": result.skip_reason,
        "validation": validation.to_dict(),
        "identity_quality": _identity_quality(statements),
        "source_coverage": _source_coverage(pdf, statements),
        "cash_reconciliation": cash_checks,
    }


def _position_checks(parsed: list[tuple[str, ParsedStatement]]) -> list[dict]:
    checkpoints: dict[tuple[str, str, str], list[tuple[str, float]]] = defaultdict(list)
    movements: dict[tuple[str, str, str], list[tuple[str, float]]] = defaultdict(list)
    for institution, statement in parsed:
        account_key = f"{institution}|{statement.account.account_number}"
        for position in statement.positions:
            key = (account_key, instrument_key(position.instrument), position.currency)
            checkpoints[key].append((statement.period_end, float(position.quantity)))
        for transaction in statement.transactions:
            if transaction.instrument is None or transaction.quantity is None:
                continue
            delta = quantity_delta(transaction.txn_type, transaction.quantity)
            if abs(delta) <= 1e-12:
                continue
            key = (account_key, instrument_key(transaction.instrument), transaction.currency)
            movements[key].append((transaction.trade_date, delta))

    checks: list[dict] = []
    for key, raw_points in checkpoints.items():
        by_date: dict[str, float] = {}
        for checkpoint_date, quantity in raw_points:
            by_date[checkpoint_date] = quantity
        points = sorted(by_date.items())
        for (prior_date, prior_quantity), (close_date, reported_close) in zip(
            points, points[1:], strict=False
        ):
            delta_sum = sum(
                delta
                for movement_date, delta in movements.get(key, [])
                if prior_date < movement_date <= close_date
            )
            expected_close = prior_quantity + delta_sum
            residual = reported_close - expected_close
            checks.append(
                {
                    "account": key[0],
                    "instrument_key": key[1],
                    "currency": key[2],
                    "prior_date": prior_date,
                    "close_date": close_date,
                    "prior_quantity": prior_quantity,
                    "delta_sum": round(delta_sum, 8),
                    "expected_close": round(expected_close, 8),
                    "reported_close": reported_close,
                    "residual": round(residual, 8),
                    "status": "balanced" if abs(residual) <= 1e-8 else "unbalanced",
                }
            )
    return checks


def _summary(records: list[dict], position_checks: list[dict]) -> dict:
    cash_checks = [
        check
        for record in records
        for statement in record.get("cash_reconciliation", [])
        for check in statement["checks"]
    ]
    counts = {
        name: sum(record.get("counts", {}).get(name, 0) for record in records)
        for name in (
            "statements", "transactions", "positions", "cash_balances",
            "annual_performance", "quarantine",
        )
    }
    return {
        "record_type": "summary",
        "files": len(records),
        "parsed_files": sum(record.get("status") == "parsed" for record in records),
        "skipped_files": sum(record.get("status") == "skipped" for record in records),
        "invalid_files": sum(record.get("status") == "invalid" for record in records),
        "unclaimed_files": sum(record.get("status") == "unclaimed" for record in records),
        "failed_files": sum(record.get("status") == "failed" for record in records),
        "validation_errors": sum(
            record.get("validation", {}).get("error_count", 0) for record in records
        ),
        "validation_warnings": sum(
            record.get("validation", {}).get("warning_count", 0) for record in records
        ),
        "duplicate_statement_keys": sum(
            issue.get("code") == "duplicate_statement_key"
            for record in records
            for issue in record.get("validation", {}).get("issues", [])
        ),
        "synthetic_or_unresolved_instruments": sum(
            record.get("identity_quality", {}).get("synthetic_or_unresolved", 0)
            for record in records
        ),
        "counts": counts,
        "cash_checks": len(cash_checks),
        "cash_unbalanced": sum(check["status"] == "unbalanced" for check in cash_checks),
        "cash_incomplete": sum(check["status"] == "incomplete" for check in cash_checks),
        "position_checks": len(position_checks),
        "position_unbalanced": sum(
            check["status"] == "unbalanced" for check in position_checks
        ),
    }


def audit_extraction(
    *,
    statements_dir: Path,
    output: Path,
    institution: str | None = None,
    limit: int | None = None,
) -> dict:
    """Parse a corpus without opening SQLite and write a deterministic JSONL report."""
    root = statements_dir.resolve()
    paths = _discover(root, institution, limit)
    records: list[dict] = []
    parsed_statements: list[tuple[str, ParsedStatement]] = []
    for path in paths:
        folder = path.parent.name
        log.debug("Auditing %s", path)
        try:
            pdf = _load_source(path, root)
        except Exception as exc:
            records.append(
                {
                    "record_type": "source",
                    "path": str(path),
                    "institution_folder": folder,
                    "source_kind": path.suffix.lower().lstrip("."),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if pdf.is_image_only:
            records.append(
                {
                    "record_type": "source",
                    "path": pdf.relpath,
                    "institution_folder": folder,
                    "source_kind": path.suffix.lower().lstrip("."),
                    "sha256": pdf.sha256,
                    "page_count": pdf.page_count,
                    "image_only": True,
                    "status": "skipped",
                    "reason": "image-only or insufficient extracted text",
                }
            )
            continue
        parser = select_parser(folder, pdf)
        if parser is None:
            records.append(
                {
                    "record_type": "source",
                    "path": pdf.relpath,
                    "institution_folder": folder,
                    "source_kind": path.suffix.lower().lstrip("."),
                    "sha256": pdf.sha256,
                    "page_count": pdf.page_count,
                    "image_only": False,
                    "status": "unclaimed",
                }
            )
            continue
        try:
            result = parser.parse(pdf)
        except Exception as exc:
            records.append(
                {
                    "record_type": "source",
                    "path": pdf.relpath,
                    "institution_folder": folder,
                    "source_kind": path.suffix.lower().lstrip("."),
                    "sha256": pdf.sha256,
                    "page_count": pdf.page_count,
                    "image_only": False,
                    "status": "failed",
                    "parser": {"name": parser.NAME, "version": parser.VERSION},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        records.append(
            _record_for_result(
                path=path,
                institution=folder,
                pdf=pdf,
                result=result,
            )
        )
        parsed_statements.extend((folder, statement) for statement in result.statements)

    position_checks = _position_checks(parsed_statements)
    summary = _summary(records, position_checks)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        for check in position_checks:
            handle.write(
                json.dumps(
                    {"record_type": "position_reconciliation", **check},
                    sort_keys=True,
                )
                + "\n"
            )
        handle.write(json.dumps(summary, sort_keys=True) + "\n")
    return summary

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from typing import Any

from trade_history.config import settings
from trade_history.parsers.base import ParsedEvent, ParsedInstrument
from trade_history.parsers.common import normalize_symbol, parse_date


LINE_REF_RE = re.compile(r"^p\d+:l\d+$", re.IGNORECASE)


@dataclass(slots=True)
class GeminiOverrides:
    events_by_line_ref: dict[str, dict[str, Any]]
    source_path: Path | None = None


def _normalize_line_ref(value: Any, payload: dict[str, Any]) -> str | None:
    if isinstance(value, str):
        line_ref = value.strip()
        if LINE_REF_RE.match(line_ref):
            return line_ref.lower()
    page = payload.get("page_number") or payload.get("page")
    line = payload.get("line_number") or payload.get("line")
    if isinstance(page, int) and isinstance(line, int):
        return f"p{page}:l{line}"
    return None


def _extract_json(value: str | None) -> dict[str, Any] | list[Any] | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, (dict, list)):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _transactions_from_payload(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if "transactions" in payload and isinstance(payload["transactions"], list):
        return [item for item in payload["transactions"] if isinstance(item, dict)]

    for key in ("response", "text", "content", "result", "output"):
        nested = payload.get(key)
        if isinstance(nested, str):
            parsed = _extract_json(nested)
            if parsed is not None:
                return _transactions_from_payload(parsed)
        if isinstance(nested, dict):
            items = _transactions_from_payload(nested)
            if items:
                return items
        if isinstance(nested, list):
            parsed_list = [item for item in nested if isinstance(item, dict)]
            if parsed_list:
                return parsed_list
    return []


def _candidate_paths(file_path: Path) -> list[Path]:
    candidates = [
        file_path.with_suffix(f"{file_path.suffix}.gemini.json"),
        file_path.with_suffix(".gemini.json"),
        settings.gemini_overrides_root / file_path.parent.name / f"{file_path.stem}.json",
        settings.gemini_overrides_root / file_path.parent.name / f"{file_path.name}.json",
    ]

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def load_event_overrides(file_path: Path) -> GeminiOverrides:
    for candidate in _candidate_paths(file_path):
        if not candidate.exists():
            continue
        parsed = _extract_json(candidate.read_text(encoding="utf-8", errors="ignore"))
        if parsed is None:
            continue
        transactions = _transactions_from_payload(parsed)
        by_line_ref: dict[str, dict[str, Any]] = {}
        for item in transactions:
            line_ref = _normalize_line_ref(
                item.get("source_line_ref") or item.get("line_ref") or item.get("sourceLineRef"),
                item,
            )
            if line_ref is None:
                continue
            by_line_ref[line_ref] = item
        if by_line_ref:
            return GeminiOverrides(events_by_line_ref=by_line_ref, source_path=candidate)
    return GeminiOverrides(events_by_line_ref={}, source_path=None)


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _parse_expiry(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return parse_date(value)
    return None


def _normalize_put_call(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if token in {"P", "PUT"}:
        return "P"
    if token in {"C", "CALL"}:
        return "C"
    return None


def apply_event_override(event: ParsedEvent, override: dict[str, Any]) -> ParsedEvent:
    symbol = override.get("symbol_norm") or override.get("symbol") or override.get("ticker")
    symbol_raw = override.get("symbol_raw") or symbol
    asset_type = str(
        override.get("asset_type")
        or (event.instrument.asset_type if event.instrument else "equity")
    ).lower()

    option_root = override.get("option_root")
    if isinstance(option_root, str):
        option_root = normalize_symbol(option_root)
    put_call = _normalize_put_call(override.get("put_call"))
    strike = _parse_float(override.get("strike"))
    expiry = _parse_expiry(override.get("expiry"))
    multiplier = override.get("multiplier")
    multiplier_int = int(multiplier) if isinstance(multiplier, int) else (100 if asset_type == "option" else 1)

    if isinstance(symbol, str) and symbol.strip():
        norm_symbol = normalize_symbol(symbol)
        instrument = event.instrument or ParsedInstrument(symbol_raw=norm_symbol, symbol_norm=norm_symbol)
        instrument.symbol_raw = str(symbol_raw or norm_symbol)
        instrument.symbol_norm = norm_symbol
        instrument.asset_type = asset_type
        if asset_type == "option":
            instrument.option_root = option_root or normalize_symbol(norm_symbol)
            instrument.put_call = put_call or instrument.put_call
            instrument.strike = strike if strike is not None else instrument.strike
            instrument.expiry = expiry or instrument.expiry
            instrument.multiplier = multiplier_int
        event.instrument = instrument

    side = override.get("side")
    if isinstance(side, str) and side.strip():
        event.side = side.strip().upper()

    event_type = override.get("event_type")
    if isinstance(event_type, str) and event_type.strip():
        event.event_type = event_type.strip().lower()

    currency = override.get("currency")
    if isinstance(currency, str) and currency.strip():
        event.currency = currency.strip().upper()

    quantity = _parse_float(override.get("quantity"))
    if quantity is not None:
        event.quantity = abs(quantity)

    price = _parse_float(override.get("price"))
    if price is not None:
        event.price = abs(price)

    gross_amount = _parse_float(override.get("gross_amount"))
    if gross_amount is not None:
        event.gross_amount = gross_amount

    commission = _parse_float(override.get("commission"))
    if commission is not None:
        event.commission = abs(commission)

    fees = _parse_float(override.get("fees"))
    if fees is not None:
        event.fees = abs(fees)

    return event

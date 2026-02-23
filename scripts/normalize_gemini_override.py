from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


LINE_REF_RE = re.compile(r"^p\d+:l\d+$", re.IGNORECASE)


def _extract_json(text: str) -> dict[str, Any] | list[Any] | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, (dict, list)):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        snippet = raw[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _find_transactions(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if "transactions" in payload and isinstance(payload["transactions"], list):
        return [item for item in payload["transactions"] if isinstance(item, dict)]

    for key in ("response", "text", "content", "result", "output"):
        nested = payload.get(key)
        if isinstance(nested, str):
            parsed = _extract_json(nested)
            if parsed is not None:
                rows = _find_transactions(parsed)
                if rows:
                    return rows
        if isinstance(nested, dict):
            rows = _find_transactions(nested)
            if rows:
                return rows
        if isinstance(nested, list):
            rows = [item for item in nested if isinstance(item, dict)]
            if rows:
                return rows
    return []


def _normalize_line_ref(item: dict[str, Any]) -> str | None:
    value = item.get("source_line_ref") or item.get("line_ref") or item.get("sourceLineRef")
    if isinstance(value, str):
        line_ref = value.strip()
        if LINE_REF_RE.match(line_ref):
            return line_ref.lower()
    page = item.get("page") or item.get("page_number")
    line = item.get("line") or item.get("line_number")
    if isinstance(page, int) and isinstance(line, int):
        return f"p{page}:l{line}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize Gemini CLI extraction output to parser override format.")
    parser.add_argument("--input", required=True, type=Path, help="Raw Gemini output file")
    parser.add_argument("--output", required=True, type=Path, help="Normalized override json file")
    parser.add_argument("--source-pdf", required=True, type=str, help="Source PDF path")
    args = parser.parse_args()

    raw_text = args.input.read_text(encoding="utf-8", errors="ignore")
    parsed = _extract_json(raw_text)
    transactions: list[dict[str, Any]] = []
    if parsed is not None:
        for item in _find_transactions(parsed):
            line_ref = _normalize_line_ref(item)
            if not line_ref:
                continue
            row = dict(item)
            row["source_line_ref"] = line_ref
            transactions.append(row)

    payload = {
        "schema_version": "gemini_override_v1",
        "source_pdf": args.source_pdf,
        "transactions": transactions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

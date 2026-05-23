"""GET/PUT /config — user preferences and portfolio profiles stored in
``data/config.json``. The schema is intentionally loose so the frontend can
add fields without a migration. Legacy ``display_currency`` keys are ignored;
currency conversion is handled by individual views instead of a global setting.

Default shape::

    {
      "portfolios": [
        {"id": "all", "name": "All accounts", "account_ids": []}
      ],
      "active_portfolio": "all",
      "theme": "dark",
      "hide_money": false
    }
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...config import DATA_DIR

router = APIRouter(prefix="/config", tags=["config"])

_CONFIG_PATH = Path(DATA_DIR) / "config.json"

_DEFAULT: dict = {
    "portfolios": [{"id": "all", "name": "All accounts", "account_ids": []}],
    "active_portfolio": "all",
    "theme": "dark",
    "hide_money": False,
    "language": "en",
    # Optional parser-draft provider keys. Stored locally in data/config.json;
    # sent only when the user explicitly runs the provider-backed draft flow.
    "llm_keys": {
        "openai": "",
        "anthropic": "",
        "google": "",
    },
}


def _read() -> dict:
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULT)
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT)
    # Merge defaults so newly-added fields appear.
    out = dict(_DEFAULT)
    out.update(data or {})
    out.pop("display_currency", None)
    if not isinstance(out.get("portfolios"), list) or not out["portfolios"]:
        out["portfolios"] = list(_DEFAULT["portfolios"])
    return out


def _write(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=False)
    tmp.replace(_CONFIG_PATH)


@router.get("")
def get_config() -> dict:
    return _read()


@router.put("")
def put_config(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="config payload must be a JSON object")
    payload = dict(payload)
    payload.pop("display_currency", None)
    # Light validation.
    portfolios = payload.get("portfolios")
    if portfolios is not None:
        if not isinstance(portfolios, list):
            raise HTTPException(status_code=400, detail="portfolios must be a list")
        seen: set[str] = set()
        for p in portfolios:
            if not isinstance(p, dict) or "id" not in p or "name" not in p:
                raise HTTPException(status_code=400, detail="each portfolio needs id+name")
            if p["id"] in seen:
                raise HTTPException(status_code=400, detail=f"duplicate portfolio id {p['id']}")
            seen.add(p["id"])
            p.setdefault("account_ids", [])
    cur = _read()
    cur.update(payload)
    cur.pop("display_currency", None)
    _write(cur)
    return cur

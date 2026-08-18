"""LLM-assisted resolution for ambiguous instrument listing candidates.

Sends only public security names/symbols and public Yahoo quote candidates
(never account/source values) to the configured chat model. Default provider
is Z.ai with GLM-5.3; override with LEDGER_LLM_BASE_URL / LEDGER_LLM_MODEL
and authenticate with ZAI_API_KEY (or ZHIPUAI_API_KEY).

The model only chooses among grounded Yahoo results — it can never introduce
a symbol that Yahoo did not return — and every choice still passes the
deterministic currency/type/price-history checks in ``yahoo_resolution``
before a mapping row changes.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_LLM_BASE_URL = "https://api.z.ai/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-5.3"

_SYSTEM_PROMPT = (
    "You resolve broker statement security lines to Yahoo Finance listings. "
    "You are given one broker-printed security (name, printed symbol, currency) "
    "and a JSON list of real Yahoo quotes. Reply with ONLY a JSON object: "
    '{"provider_symbol": "<exact symbol from the list or null>", '
    '"reason": "<one short sentence>"}. Choose the single quote that is the '
    "same company or fund listing trading in the given currency. If the broker "
    "name is too generic to identify the exact listing, or none of the quotes "
    "matches, use null. Never output a symbol that is not in the list."
)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def llm_base_url() -> str:
    return os.environ.get("LEDGER_LLM_BASE_URL") or DEFAULT_LLM_BASE_URL


def llm_model() -> str:
    return os.environ.get("LEDGER_LLM_MODEL") or DEFAULT_LLM_MODEL


def llm_api_key() -> str | None:
    return os.environ.get("ZAI_API_KEY") or os.environ.get("ZHIPUAI_API_KEY")


def _is_local_host(hostname: str) -> bool:
    if not hostname:
        return True
    lowered = hostname.lower().strip("[]")
    if lowered in {"localhost", "0.0.0.0"} or lowered.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return bool(
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_outbound_base_url(base_url: str) -> str:
    """Allow only http(s) base URLs whose host is a public address."""
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"}:
        raise ValueError(f"LLM base URL must be http(s): {base_url!r}")
    if not parts.hostname or _is_local_host(parts.hostname):
        raise ValueError(
            "LLM base URL host must be a public address "
            f"(loopback/private/reserved hosts are rejected): {base_url!r}"
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(f"LLM base URL must not carry userinfo/query/fragment: {base_url!r}")
    return base_url


def parse_choice(content: str, quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Parse the model reply and return the chosen grounded quote, if any."""
    text = (content or "").strip()
    text = _FENCE_RE.sub("", text).strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    symbol = payload.get("provider_symbol")
    if not isinstance(symbol, str):
        return None
    symbol_upper = symbol.upper()
    for quote in quotes:
        if str(quote.get("symbol") or "").upper() == symbol_upper:
            return quote
    return None


class LlmResolver:
    """Chat-model tiebreaker for ambiguous Yahoo listing candidates."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 60.0,
        sleep_s: float = 0.5,
        attempts: int = 2,
    ) -> None:
        self._api_key = api_key
        self._base_url = validate_outbound_base_url(base_url or llm_base_url())
        self._model = model or llm_model()
        self._timeout_s = timeout_s
        self._sleep_s = sleep_s
        self._attempts = max(1, attempts)

    def describe(self) -> str:
        parts = urlsplit(self._base_url)
        return f"{parts.scheme}://{parts.hostname} model={self._model}"

    def _chat(self, user_payload: dict[str, Any]) -> str | None:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0,
            "max_tokens": 200,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error: Exception | None = None
        for attempt in range(self._attempts):
            if attempt:
                time.sleep(self._sleep_s)
            try:
                with httpx.Client(
                    base_url=self._base_url,
                    headers=headers,
                    timeout=self._timeout_s,
                    follow_redirects=False,
                ) as client:
                    response = client.post("/chat/completions", json=body)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return str(content) if content is not None else None
            except Exception as exc:  # network/HTTP/schema errors: retry then give up
                last_error = exc
        if last_error is not None:
            raise last_error
        return None

    def choose(
        self,
        *,
        name: str,
        symbol: str | None,
        currency: str,
        quotes: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        """Return (chosen grounded quote or None, reason) for one listing."""
        if not quotes:
            return None, "no grounded candidates"
        slim_quotes = [
            {
                "symbol": quote.get("symbol"),
                "name": quote.get("longname") or quote.get("shortname"),
                "type": quote.get("quoteType"),
                "exchange": quote.get("exchange") or quote.get("fullExchangeName"),
                "currency": quote.get("currency"),
            }
            for quote in quotes
        ]
        payload = {
            "broker_name": name,
            "printed_symbol": symbol,
            "currency": currency,
            "quotes": slim_quotes,
        }
        try:
            content = self._chat(payload)
        except Exception as exc:
            return None, f"llm call failed: {str(exc)[:120]}"
        if content is None:
            return None, "llm returned no content"
        chosen = parse_choice(content, quotes)
        if chosen is None:
            return None, "llm choice not grounded in candidate list"
        return chosen, "llm assisted"

"""LLM-assisted listing resolution: client guards and resolver integration."""
from __future__ import annotations

import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest import yahoo_resolution
from ledger.ingest.llm_resolution import (
    LlmResolver,
    parse_choice,
    validate_outbound_base_url,
)
from ledger.ingest.yahoo_resolution import verify_yahoo_identities


class FakeLlm:
    def __init__(self, pick=None, raise_error: bool = False):
        self._pick = pick
        self._raise = raise_error
        self.calls: list[dict] = []

    def choose(self, *, name, symbol, currency, quotes):
        self.calls.append(
            {"name": name, "symbol": symbol, "currency": currency, "quotes": quotes}
        )
        if self._raise:
            raise RuntimeError("llm unavailable")
        if self._pick is None:
            return None, "declined"
        return self._pick(quotes), "llm assisted"


# ---------------------------------------------------------------- client guards

@pytest.mark.parametrize(
    "url",
    [
        "https://api.z.ai/api/paas/v4",
        "https://open.bigmodel.cn/api/paas/v4",
    ],
)
def test_outbound_base_url_accepts_public_https_hosts(url):
    assert validate_outbound_base_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v4",
        "http://localhost/api",
        "https://192.168.1.5/api/paas/v4",
        "https://10.0.0.1/api",
        "https://[::1]/api",
        "ftp://api.z.ai/api",
        "https://user:pass@api.z.ai/api",
        "https://api.z.ai/api?x=1",
        "not a url",
    ],
)
def test_outbound_base_url_rejects_local_or_malformed(url):
    with pytest.raises(ValueError):
        validate_outbound_base_url(url)


def test_parse_choice_requires_grounded_symbol():
    quotes = [
        {"symbol": "EXMP.TO", "shortname": "Example Canadian ETF"},
        {"symbol": "OTHER.TO", "shortname": "Other ETF"},
    ]
    assert parse_choice('{"provider_symbol": "EXMP.TO", "reason": "r"}', quotes) is quotes[0]
    assert (
        parse_choice('```json\n{"provider_symbol": "other.to"}\n```', quotes) is quotes[1]
    )
    assert parse_choice('{"provider_symbol": "NOT-IN-LIST"}', quotes) is None
    assert parse_choice('{"provider_symbol": null}', quotes) is None
    assert parse_choice("not json at all", quotes) is None
    assert parse_choice('["array"]', quotes) is None


def test_resolver_describe_hides_key_and_path():
    resolver = LlmResolver(
        "secret-key",
        base_url="https://api.z.ai/api/paas/v4",
        model="glm-5.3",
    )
    description = resolver.describe()
    assert "secret-key" not in description
    assert "model=glm-5.3" in description


# ------------------------------------------------------- resolver integration

def _unmapped_instrument(conn, *, symbol: str, name: str, currency: str,
                         option_root: str | None = None) -> int:
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
    account_id = sqlite_db.upsert_account(
        conn, institution_id=institution_id, account_number="A-1"
    )
    instrument_id = sqlite_db.upsert_instrument(
        conn,
        asset_type="equity",
        symbol=symbol,
        currency=currency,
        exchange=None,
        name=name,
        option_root=option_root,
    )
    conn.execute(
        """
        INSERT INTO transactions(
            account_id, trade_date, txn_type, instrument_id, quantity,
            position_delta, currency
        ) VALUES (?, '2024-01-10', 'buy', ?, 1, 1, ?)
        """,
        (account_id, instrument_id, currency),
    )
    return instrument_id


@pytest.fixture(autouse=True)
def _hermetic_audit_log(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yahoo_resolution, "jsonl_path", lambda name: tmp_path / f"{name}.jsonl"
    )


def test_llm_resolves_ambiguous_pending_candidate(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        sqlite_db.upsert_institution(conn, "HSBC_IDI", "HSBC")
        sqlite_db.queue_instrument_resolution_candidate(
            conn,
            institution_code="HSBC_IDI",
            normalized_text="EXAMPLECANADIANETF",
            display_text="Example Canadian ETF",
            asset_type="etf",
            currency="CAD",
        )
    quotes = [
        {"symbol": "EXMP.TO", "shortname": "Example Canadian ETF", "quoteType": "ETF"},
        {"symbol": "EXMP2.TO", "shortname": "Example Canadian ETF", "quoteType": "ETF"},
    ]
    llm = FakeLlm(pick=lambda grounded: grounded[1])

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: quotes,
        history=lambda symbol: symbol == "EXMP2.TO",
        quote_info=lambda _symbol: None,
        llm=llm,
    )
    with sqlite_db.session(db_path) as conn:
        row = conn.execute(
            """
            SELECT candidate.status, candidate.resolution_method,
                   market.provider_symbol, market.status AS market_status
              FROM instrument_resolution_candidates candidate
              JOIN instruments i ON i.instrument_id = candidate.resolved_instrument_id
              JOIN instrument_market_symbols market
                ON market.instrument_id = i.instrument_id
            """
        ).fetchone()

    assert result["candidates_llm_proposed"] == 1
    assert result["candidates_resolved_llm"] == 1
    assert tuple(row) == ("resolved", "llm_assisted", "EXMP2.TO", "verified")
    # the model only ever saw currency-consistent grounded quotes
    assert [q["symbol"] for q in llm.calls[0]["quotes"]] == ["EXMP.TO", "EXMP2.TO"]


def test_llm_ungrounded_choice_is_rejected_for_candidate(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        sqlite_db.upsert_institution(conn, "HSBC_IDI", "HSBC")
        sqlite_db.queue_instrument_resolution_candidate(
            conn,
            institution_code="HSBC_IDI",
            normalized_text="EXAMPLECANADIANETF",
            display_text="Example Canadian ETF",
            asset_type="etf",
            currency="CAD",
        )
    quotes = [
        {"symbol": "EXMP.TO", "shortname": "Example Canadian ETF", "quoteType": "ETF"},
        {"symbol": "EXMP2.TO", "shortname": "Example Canadian ETF", "quoteType": "ETF"},
    ]
    impostor = {"symbol": "EVIL.TO", "shortname": "Evil ETF", "quoteType": "ETF"}

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: quotes,
        history=lambda _symbol: True,
        quote_info=lambda _symbol: None,
        llm=FakeLlm(pick=lambda _grounded: impostor),
    )
    with sqlite_db.session(db_path) as conn:
        status = conn.execute(
            "SELECT status FROM instrument_resolution_candidates"
        ).fetchone()[0]

    assert result.get("candidate_ambiguous") == 1
    assert status == "ambiguous"


def test_llm_resolves_unmapped_instrument_from_search_results(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _unmapped_instrument(conn, symbol="CLS", name="CELESTICA INC SV", currency="CAD")
    quotes = [
        {"symbol": "CLS.TO", "longname": "Celestica Inc.",
         "quoteType": "EQUITY", "currency": "CAD"},
        {"symbol": "CLS", "longname": "Celestica Inc.",
         "quoteType": "EQUITY", "currency": "USD"},
    ]

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: quotes,
        history=lambda symbol: symbol == "CLS.TO",
        quote_info=lambda _symbol: None,  # symbol-first pass finds nothing
        llm=FakeLlm(pick=lambda grounded: grounded[0]),
    )
    with sqlite_db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT market.provider_symbol, market.status, i.exchange,
                   i.resolution_method
              FROM instruments i
              JOIN instrument_market_symbols market
                ON market.instrument_id = i.instrument_id
            """
        ).fetchone()

    assert result["symbol_llm_resolved"] == 1
    assert tuple(rows) == ("CLS.TO", "verified", "TSX", "llm_assisted_yahoo")


def test_llm_pass_skips_contract_rows(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _unmapped_instrument(
            conn,
            symbol="SOXS",
            name="CALL SOXS JAN 17 2025 20",
            currency="USD",
            option_root="SOXS",
        )
    llm = FakeLlm(pick=lambda grounded: grounded[0] if grounded else None)

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: [],
        history=lambda _symbol: True,
        quote_info=lambda _symbol: None,
        llm=llm,
    )

    assert result.get("symbol_llm_skipped_contract") == 1
    assert llm.calls == []


def test_llm_name_floor_blocks_weak_matches(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _unmapped_instrument(conn, symbol="ZZZ", name="TOTALLY UNRELATED NAME", currency="USD")
    quotes = [
        {"symbol": "ZZZ", "longname": "Zeta Zeta Zeta Holdings Ltd.",
         "quoteType": "EQUITY", "currency": "USD"},
    ]

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: quotes,
        history=lambda _symbol: True,
        quote_info=lambda _symbol: None,
        llm=FakeLlm(pick=lambda grounded: grounded[0]),
    )
    with sqlite_db.session(db_path) as conn:
        count = conn.execute(
            "SELECT count(*) FROM instrument_market_symbols"
        ).fetchone()[0]

    assert result["symbol_llm_name_floor"] == 1
    assert count == 0


def test_llm_decline_leaves_instrument_unmapped(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        _unmapped_instrument(conn, symbol="CLS", name="CELESTICA INC SV", currency="CAD")

    result = verify_yahoo_identities(
        db_path,
        search=lambda _query: [
            {"symbol": "CLS.TO", "longname": "Celestica Inc.",
             "quoteType": "EQUITY", "currency": "CAD"},
        ],
        history=lambda _symbol: True,
        quote_info=lambda _symbol: None,
        llm=FakeLlm(pick=None),
    )

    assert result.get("symbol_llm_declined") == 1

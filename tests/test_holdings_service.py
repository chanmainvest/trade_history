"""Regression coverage for the canonical read-only holdings service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
from hypothesis import Phase, given, settings
from hypothesis import strategies as st

from ledger import holdings as holdings_service
from ledger.api.routes import monthly as monthly_route
from ledger.api.routes.performance import _total_rows
from ledger.api.routes.viz import _held_symbols_at
from ledger.db import sqlite as sqlite_db
from ledger.holdings import holdings_at

from .db_fixtures import (
    seed_cash,
    seed_position,
    seed_snapshot_set,
    seed_source,
    seed_statement,
)

_COMPOSITE_DEFECT_SCENARIOS = (
    "same_date_reported_leakage",
    "cross_date_metadata_mixing",
    "stale_valuation_leakage",
    "distinct_source_geometry",
    "same_statement_multiple_scopes",
)


@dataclass(frozen=True)
class _CompositePositionInput:
    quantities: tuple[float, ...]
    permutation: tuple[int, ...]
    independent_market_price: float


@st.composite
def _composite_position_inputs(draw):
    contributor_count = draw(st.integers(min_value=2, max_value=8))
    quantities = tuple(
        float(value)
        for value in draw(
            st.lists(
                st.integers(min_value=1, max_value=1_000),
                min_size=contributor_count,
                max_size=contributor_count,
            )
        )
    )
    permutation = tuple(draw(st.permutations(tuple(range(contributor_count)))))
    independent_market_price = float(draw(st.integers(min_value=1, max_value=1_000)))
    return _CompositePositionInput(quantities, permutation, independent_market_price)


def _composite_as_of(scenario: str) -> str:
    if scenario in {"stale_valuation_leakage", "distinct_source_geometry"}:
        return "2024-03-15"
    return "2024-02-28"


def _composite_states(
    scenario: str,
    quantities: tuple[float, ...],
) -> list[holdings_service._SecurityState]:
    states: list[holdings_service._SecurityState] = []
    for index, quantity in enumerate(quantities):
        if scenario == "cross_date_metadata_mixing" and index == 0:
            checkpoint_date = "2024-01-31"
        elif scenario == "stale_valuation_leakage":
            checkpoint_date = "2024-01-31"
        else:
            checkpoint_date = "2024-02-28"
        statement_id = 700 if scenario == "same_statement_multiple_scopes" else 700 + index
        snapshot_set_id = 800 + index
        geometry_status = "exact" if index % 2 == 0 else "unique_tokens"
        movement_ref = {
            "statement_id": statement_id,
            "kind": "transaction",
            "id": 1_000 + index,
            "geometry_status": geometry_status,
            "page_numbers": [index + 11],
            "linkable": True,
        }
        states.append(
            holdings_service._SecurityState(
                account_id=1,
                currency="CAD",
                scope_key=f"scope-{index}",
                instrument_id=41,
                instrument_key="equity|ABC|CAD",
                symbol="ABC",
                pricing_symbol="ABC.TO",
                asset_type="equity",
                option_expiry=None,
                option_strike=None,
                option_type=None,
                quantity=quantity,
                source_snapshot_id=900 + index,
                source_geometry_status=geometry_status,
                source_page_numbers=(index + 1,),
                anchor=holdings_service._ScopeAnchor(
                    snapshot_set_id=snapshot_set_id,
                    statement_id=statement_id,
                    account_id=1,
                    as_of_date=checkpoint_date,
                    currency="CAD",
                    scope_key=f"scope-{index}",
                ),
                initial_date=checkpoint_date,
                anchor_quantity=quantity,
                avg_cost=10.0 + index,
                book_value=quantity * (10.0 + index),
                market_price=20.0 + index,
                market_value=quantity * (20.0 + index),
                unrealized_pnl=quantity * (10.0 + index),
                warnings={f"contributor_warning_{index}"},
                lineage_key="security|shared-abc",
                ticker_symbols=("ABC",),
                anchor_instrument_id=41,
                movement_source_refs=[movement_ref],
            )
        )
    return states


def _source_signature(source_ref: dict | None) -> tuple[object, ...] | None:
    if source_ref is None:
        return None
    return (
        source_ref.get("statement_id"),
        source_ref.get("kind"),
        source_ref.get("id"),
        source_ref.get("geometry_status"),
        tuple(source_ref.get("page_numbers") or ()),
        source_ref.get("linkable"),
    )


def _movement_signatures(movement_refs: list[dict]) -> tuple[tuple[object, ...] | None, ...]:
    return tuple(_source_signature(source_ref) for source_ref in movement_refs)


def _bundle_signature(bundle: dict) -> tuple[object, ...]:
    return (
        bundle.get("checkpoint_date"),
        bundle.get("checkpoint_statement_id"),
        bundle.get("checkpoint_snapshot_set_id"),
        bundle.get("scope_key"),
        _source_signature(bundle.get("source_ref")),
        _movement_signatures(bundle.get("movements") or []),
    )


def _expected_bundle_signature(
    state: holdings_service._SecurityState,
) -> tuple[object, ...]:
    assert state.anchor is not None
    return (
        state.anchor.as_of_date,
        state.anchor.statement_id,
        state.anchor.snapshot_set_id,
        state.scope_key,
        (
            state.anchor.statement_id,
            "position",
            state.source_snapshot_id,
            state.source_geometry_status,
            state.source_page_numbers,
            True,
        ),
        _movement_signatures(state.movement_source_refs),
    )


def _serialize_composite(
    states: list[holdings_service._SecurityState],
    *,
    as_of: str,
    independent_market_price: float | None,
) -> dict:
    reconciliation_results = {
        (state.anchor.snapshot_set_id, state.instrument_id): {
            "status": "reconciled" if index % 2 == 0 else "within_rounding",
            "reason": f"contributor-{index}-result",
        }
        for index, state in enumerate(states)
        if state.anchor is not None
    }
    market_path = Path("temp") / "hypothesis-composite-market.duckdb"
    market_path.unlink(missing_ok=True)
    market = duckdb.connect(str(market_path))
    try:
        market.execute(
            "CREATE TABLE daily_prices("
            "symbol VARCHAR, close DOUBLE, adj_close DOUBLE, trade_date DATE)"
        )
        if independent_market_price is not None:
            market.execute(
                "INSERT INTO daily_prices VALUES ('ABC.TO', ?, ?, ?)",
                (independent_market_price, independent_market_price, as_of),
            )
    finally:
        market.close()

    try:
        combined = holdings_service._combine_security_states(states)
        record = holdings_service._security_record(
            combined,
            as_of=as_of,
            account={
                "account_number": "A-1",
                "nickname": None,
                "institution_code": "TST",
                "institution_name": "Test Broker",
            },
            reconciliation_results=reconciliation_results,
        )
        holdings_service._apply_security_prices(
            [record],
            as_of=as_of,
            market_path=market_path,
        )
        return holdings_service._finalize_records([record])[0]
    finally:
        market_path.unlink(missing_ok=True)


# Pre-fix exploration evidence (expected failure, retained for Task 3.8 comparison):
# Hypothesis minimized to _CompositePositionInput(quantities=(1.0, 1.0),
# permutation=(0, 1), independent_market_price=1.0). In the same-date case,
# scope-1 leaked source 701/901 (page 2), checkpoint 701/801, reconciliation
# within_rounding, reported_row provenance, avg/book/price/value/P&L
# 11/11/21/21/11, and broker_reported status. Cross-date output mixed the
# 2024-01-31 checkpoint date with later contributor IDs 701/801/901. The stale
# case leaked price 21 and aggregate value 42 as stale_checkpoint. Distinct
# geometry retained only position page 2 and transaction page 12. Two scopes
# from statement 700 retained only scope-1/snapshot 801/source 901 and reported
# valuation. Every scenario omitted provenance.checkpoints and non-primary
# movements, and mutating the combined movement list changed an input state.
@given(case=_composite_position_inputs())
@settings(
    max_examples=30,
    derandomize=True,
    database=None,
    deadline=None,
    phases=(Phase.generate, Phase.shrink),
)
def test_composite_position_holdings_do_not_inherit_singular_facts(case):
    """Property 1: composite positions quarantine unsupported singular facts.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2,
    2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9**
    """
    failures: list[dict] = []
    for scenario in _COMPOSITE_DEFECT_SCENARIOS:
        as_of = _composite_as_of(scenario)
        quote_price = (
            case.independent_market_price
            if scenario in {"same_date_reported_leakage", "distinct_source_geometry"}
            else None
        )
        canonical_states = _composite_states(scenario, case.quantities)
        permuted_states = [
            _composite_states(scenario, case.quantities)[index]
            for index in case.permutation
        ]
        record = _serialize_composite(
            canonical_states,
            as_of=as_of,
            independent_market_price=quote_price,
        )
        permuted_record = _serialize_composite(
            permuted_states,
            as_of=as_of,
            independent_market_price=quote_price,
        )

        contributor_order = sorted(
            canonical_states,
            key=lambda state: (
                state.anchor.as_of_date,
                state.anchor.statement_id,
                state.anchor.snapshot_set_id,
                state.scope_key,
            ),
        )
        expected_bundles = tuple(
            _expected_bundle_signature(state) for state in contributor_order
        )
        actual_bundles = record.get("provenance", {}).get("checkpoints") or []
        actual_bundle_signatures = tuple(
            _bundle_signature(bundle) for bundle in actual_bundles
        )
        bundle_quantities_are_consistent = len(actual_bundles) == len(contributor_order) and all(
            "quantity" not in bundle or bundle["quantity"] == state.quantity
            for bundle, state in zip(actual_bundles, contributor_order, strict=True)
        )
        expected_movements = tuple(
            signature
            for state in contributor_order
            for signature in _movement_signatures(state.movement_source_refs)
        )
        actual_movements = _movement_signatures(
            record.get("provenance", {}).get("movements") or []
        )

        leak_probe_states = _composite_states(scenario, case.quantities)
        leak_probe = holdings_service._combine_security_states(leak_probe_states)
        sentinel = {"kind": "transaction", "id": -1}
        leak_probe.movement_source_refs.append(sentinel)
        shallow_copy_leak = any(
            sentinel in state.movement_source_refs for state in leak_probe_states
        )

        checks = {
            "summed_quantity": record["quantity"] == sum(case.quantities),
            "account_and_native_currency_preserved": (
                record["account_id"] == 1 and record["currency"] == "CAD"
            ),
            "incomplete_non_reported_state": (
                record["holding_state"] == "incomplete"
                and record["is_reported"] is False
                and record["is_reconstructed"] is True
            ),
            "null_singular_scope_source_checkpoint_reconciliation": (
                record["scope_key"] is None
                and record["source_ref"] is None
                and record["checkpoint_date"] is None
                and record["checkpoint_statement_id"] is None
                and record["checkpoint_snapshot_set_id"] is None
                and record["reconciliation_status"] is None
                and record["reconciliation_reason"] is None
            ),
            "multiple_checkpoint_provenance": (
                record["provenance"]["type"] == "multiple_checkpoints"
                and record["provenance"]["checkpoint"] is None
            ),
            "internally_consistent_ordered_contributors": (
                actual_bundle_signatures == expected_bundles
                and bundle_quantities_are_consistent
            ),
            "stable_complete_movement_union": (
                actual_movements == expected_movements
                and len(actual_movements) == len(set(actual_movements))
            ),
            "contributor_warnings_preserved": all(
                f"contributor_warning_{index}" in record["quality_warnings"]
                for index in range(len(case.quantities))
            ),
            "no_shallow_copy_leak": not shallow_copy_leak,
            "permutation_invariant": record == permuted_record,
            "null_broker_valuation": (
                record["avg_cost"] is None
                and record["book_value"] is None
                and record["unrealized_pnl"] is None
                and record["price_status"] not in {"broker_reported", "stale_checkpoint"}
            ),
            "independent_market_valuation_only": (
                record["market_price"] == quote_price
                and record["market_value"] == quote_price * sum(case.quantities)
                and record["price_date"] == as_of
                and record["price_status"] == "market"
                if quote_price is not None
                else record["market_price"] is None
                and record["market_value"] is None
                and record["price_date"] is None
                and record["price_status"] == "unpriced"
            ),
        }
        violations = sorted(name for name, passed in checks.items() if not passed)
        if violations:
            failures.append(
                {
                    "scenario": scenario,
                    "violations": violations,
                    "observed_singular_fields": {
                        "scope_key": record["scope_key"],
                        "source_ref": record["source_ref"],
                        "checkpoint_date": record["checkpoint_date"],
                        "checkpoint_statement_id": record["checkpoint_statement_id"],
                        "checkpoint_snapshot_set_id": record["checkpoint_snapshot_set_id"],
                        "reconciliation_status": record["reconciliation_status"],
                        "reconciliation_reason": record["reconciliation_reason"],
                        "provenance": record["provenance"],
                        "avg_cost": record["avg_cost"],
                        "book_value": record["book_value"],
                        "market_price": record["market_price"],
                        "market_value": record["market_value"],
                        "unrealized_pnl": record["unrealized_pnl"],
                        "price_date": record["price_date"],
                        "price_status": record["price_status"],
                    },
                    "shallow_copy_movement_leak": shallow_copy_leak,
                }
            )

    assert not failures, (
        "Composite quantity inherited unsupported singular contributor facts: "
        f"input={case!r}; failures={failures!r}"
    )


def _account(conn, number: str = "A-1") -> int:
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    return sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number=number,
        account_type="Margin",
        base_currency="CAD",
    )


def _statement(conn, account_id: int, month: str) -> int:
    source_id = seed_source(conn, f"Statements/Test/{month}.pdf")
    return seed_statement(
        conn,
        account_id=account_id,
        source_file_id=source_id,
        period_start=f"{month}-01",
        period_end=f"{month}-28",
    )


def _transaction(
    conn,
    *,
    account_id: int,
    statement_id: int,
    trade_date: str,
    txn_type: str,
    instrument_id: int | None,
    quantity: float | None,
    position_delta: float | None,
    cash_delta: float | None,
    cash_effective_date: str,
    currency: str,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions(
            account_id, statement_id, trade_date, txn_type, instrument_id,
            quantity, position_delta, net_amount, cash_delta,
            cash_effective_date, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            statement_id,
            trade_date,
            txn_type,
            instrument_id,
            quantity,
            position_delta,
            cash_delta,
            cash_delta,
            cash_effective_date,
            currency,
        ),
    )


def _seed_reconciled_position_result(conn, *, account_id: int, instrument_id: int) -> None:
    snapshot_set_id = conn.execute(
        "SELECT snapshot_set_id FROM position_snapshots WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO reconciliation_results(
            reconciliation_key, kind, account_id, snapshot_set_id, instrument_id,
            currency, tolerance, status
        ) VALUES (?, 'position', ?, ?, ?, 'CAD', 0.00000001, 'reconciled')
        """,
        (f"recon:v1:position:{snapshot_set_id}:{instrument_id}", account_id, snapshot_set_id, instrument_id),
    )


def test_holdings_reprices_post_checkpoint_movement_without_recomputing_cost(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    market_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        conn.execute(
            "UPDATE position_snapshots SET market_price = 10, avg_cost = 8, book_value = 80"
        )
        seed_cash(conn, statement_id=jan, currency="CAD", opening_balance=50, closing_balance=50)
        _seed_reconciled_position_result(conn, account_id=account_id, instrument_id=instrument_id)
        _transaction(
            conn,
            account_id=account_id,
            statement_id=jan,
            trade_date="2024-02-05",
            txn_type="buy",
            instrument_id=instrument_id,
            quantity=2,
            position_delta=2,
            cash_delta=-20,
            cash_effective_date="2024-02-06",
            currency="CAD",
        )
    con = duckdb.connect(str(market_path))
    try:
        con.execute(
            "CREATE TABLE daily_prices(symbol VARCHAR, close DOUBLE, adj_close DOUBLE, trade_date DATE)"
        )
        con.execute("INSERT INTO daily_prices VALUES ('ABC', 12, 12, '2024-02-10')")
    finally:
        con.close()

    rows = holdings_at("2024-02-15", path=db_path, market_path=market_path)
    security = next(row for row in rows if row["asset_type"] == "equity")
    cash = next(row for row in rows if row["asset_type"] == "cash")

    assert security["quantity"] == 12.0
    assert security["market_price"] == 12.0
    assert security["market_value"] == 144.0
    assert security["price_date"] == "2024-02-10"
    assert security["price_status"] == "market"
    assert security["checkpoint_date"] == "2024-01-28"
    assert security["checkpoint_statement_id"] == jan
    assert security["source_ref"] == {
        "statement_id": jan,
        "kind": "position",
        "id": security["source_ref"]["id"],
        "checkpoint": True,
        "geometry_status": "unavailable",
        "page_numbers": [],
        "linkable": False,
    }
    assert security["is_reported"] is False
    assert security["is_reconstructed"] is True
    assert security["holding_state"] == "reconstructed"
    assert security["reconciliation_status"] == "reconciled"
    assert security["book_value"] is None
    assert security["unrealized_pnl"] is None
    assert cash["quantity"] == 30.0
    assert cash["holding_state"] == "reconstructed"


def test_incomplete_scope_keeps_prior_anchor_but_marks_the_holding(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        abc_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        xyz_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="XYZ",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=abc_id,
            quantity=10,
            market_value=100,
            currency="CAD",
            completeness="complete",
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=xyz_id,
            quantity=5,
            market_value=50,
            currency="CAD",
            completeness="partial",
        )

    rows = holdings_at("2024-02-28", path=db_path)

    assert [(row["symbol"], row["quantity"]) for row in rows] == [("ABC", 10.0)]
    assert rows[0]["holding_state"] == "incomplete"
    assert "incomplete_position_scope_after_checkpoint" in rows[0]["quality_warnings"]


def test_complete_checkpoint_omission_does_not_revive_obsolete_initial_position(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        january = _statement(conn, account_id, "2024-01")
        february = _statement(conn, account_id, "2024-02")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        conn.execute(
            """
            INSERT INTO initial_positions(
                account_id, as_of_date, instrument_id, quantity, currency, notes
            ) VALUES (?, '2023-12-31', ?, -10, 'CAD', 'inferred: synthetic regression')
            """,
            (account_id, instrument_id),
        )
        _transaction(
            conn,
            account_id=account_id,
            statement_id=january,
            trade_date="2024-01-10",
            txn_type="buy",
            instrument_id=instrument_id,
            quantity=10,
            position_delta=10,
            cash_delta=-100,
            cash_effective_date="2024-01-10",
            currency="CAD",
        )
        seed_snapshot_set(
            conn,
            statement_id=february,
            currency="CAD",
            section_type="positions",
            completeness="complete",
        )

    rows = holdings_at("2024-03-15", path=db_path)

    assert all(row["symbol"] != "ABC" for row in rows)


def test_performance_stops_forward_filling_stale_accounts(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    fresh_date = (date.today() - timedelta(days=30)).isoformat()
    stale_date = (date.today() - timedelta(days=200)).isoformat()
    with sqlite_db.session(db_path) as conn:
        fresh_account = _account(conn, "FRESH")
        stale_account = _account(conn, "STALE")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        fresh_statement = seed_statement(
            conn,
            account_id=fresh_account,
            source_file_id=seed_source(conn, "Statements/Test/fresh.pdf"),
            period_start=fresh_date,
            period_end=fresh_date,
        )
        stale_statement = seed_statement(
            conn,
            account_id=stale_account,
            source_file_id=seed_source(conn, "Statements/Test/stale.pdf"),
            period_start=stale_date,
            period_end=stale_date,
        )
        seed_position(
            conn,
            statement_id=fresh_statement,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=stale_statement,
            instrument_id=instrument_id,
            quantity=20,
            market_value=200,
            currency="CAD",
        )

    current = [
        row
        for row in _total_rows(path=db_path)
        if row["as_of_date"] == date.today().isoformat() and row["currency"] == "CAD"
    ]

    assert current == [
        {
            "as_of_date": date.today().isoformat(),
            "currency": "CAD",
            "market_value": 100.0,
        }
    ]


def test_unscoped_movements_are_not_fanned_out_across_complete_scopes(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        january = _statement(conn, account_id, "2024-01")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_cash(
            conn,
            statement_id=january,
            currency="CAD",
            opening_balance=100,
            closing_balance=100,
        )
        evidence_id = conn.execute(
            "SELECT evidence_id FROM position_snapshots LIMIT 1"
        ).fetchone()[0]
        position_scope_id = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=january,
            account_id=account_id,
            as_of_date="2024-01-28",
            currency="CAD",
            section_type="positions",
            scope_key="secondary",
            completeness="complete",
            evidence_id=evidence_id,
            reported_total=None,
            validation_status="valid",
        )
        cash_scope_id = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=january,
            account_id=account_id,
            as_of_date="2024-01-28",
            currency="CAD",
            section_type="cash",
            scope_key="secondary",
            completeness="complete",
            evidence_id=evidence_id,
            reported_total=None,
            validation_status="valid",
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(
                statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
                instrument_id, quantity, market_value, currency
            ) VALUES (?, ?, ?, ?, '2024-01-28', ?, 20, 200, 'CAD')
            """,
            (january, position_scope_id, evidence_id, account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(
                statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
                currency, opening_balance, closing_balance
            ) VALUES (?, ?, ?, ?, '2024-01-28', 'CAD', 200, 200)
            """,
            (january, cash_scope_id, evidence_id, account_id),
        )
        _transaction(
            conn,
            account_id=account_id,
            statement_id=january,
            trade_date="2024-02-05",
            txn_type="buy",
            instrument_id=instrument_id,
            quantity=2,
            position_delta=2,
            cash_delta=-10,
            cash_effective_date="2024-02-05",
            currency="CAD",
        )

    rows = holdings_at("2024-02-15", path=db_path)
    security = next(row for row in rows if row["asset_type"] == "equity")
    cash = next(row for row in rows if row["asset_type"] == "cash")

    assert security["quantity"] == 30.0
    assert cash["quantity"] == 300.0
    assert security["holding_state"] == "incomplete"
    assert cash["holding_state"] == "incomplete"
    assert "ambiguous_position_scope_transaction" in security["quality_warnings"]
    assert "ambiguous_cash_scope_transaction" in cash["quality_warnings"]


def test_option_does_not_use_its_underlying_quote_as_a_contract_price(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    market_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        january = _statement(conn, account_id, "2024-01")
        option_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="option",
            symbol="ABC",
            currency="CAD",
            option_root="ABC",
            option_expiry="2024-06-21",
            option_strike=100,
            option_type="CALL",
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=option_id,
            quantity=1,
            market_value=5,
            currency="CAD",
        )
        conn.execute("UPDATE position_snapshots SET market_price = 5")
        _transaction(
            conn,
            account_id=account_id,
            statement_id=january,
            trade_date="2024-02-05",
            txn_type="option_buy_to_open",
            instrument_id=option_id,
            quantity=1,
            position_delta=1,
            cash_delta=-5,
            cash_effective_date="2024-02-05",
            currency="CAD",
        )
    con = duckdb.connect(str(market_path))
    try:
        con.execute(
            "CREATE TABLE daily_prices(symbol VARCHAR, close DOUBLE, adj_close DOUBLE, trade_date DATE)"
        )
        con.execute("INSERT INTO daily_prices VALUES ('ABC', 100, 100, '2024-02-10')")
    finally:
        con.close()

    option = holdings_at("2024-02-15", path=db_path, market_path=market_path)[0]

    assert option["quantity"] == 2.0
    assert option["market_price"] == 5.0
    assert option["market_value"] == 10.0
    assert option["price_status"] == "stale_checkpoint"
    assert "stale_checkpoint_price" in option["quality_warnings"]


def test_option_does_not_share_equity_quote_when_both_have_same_root(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    market_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        january = _statement(conn, account_id, "2024-01")
        equity_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        option_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="option",
            symbol="ABC",
            currency="CAD",
            option_root="ABC",
            option_expiry="2024-06-21",
            option_strike=100,
            option_type="CALL",
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=equity_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=option_id,
            quantity=1,
            market_value=5,
            currency="CAD",
        )
        conn.execute(
            "UPDATE position_snapshots SET market_price = 10 WHERE instrument_id = ?",
            (equity_id,),
        )
        conn.execute(
            "UPDATE position_snapshots SET market_price = 5 WHERE instrument_id = ?",
            (option_id,),
        )
    con = duckdb.connect(str(market_path))
    try:
        con.execute(
            "CREATE TABLE daily_prices(symbol VARCHAR, close DOUBLE, "
            "adj_close DOUBLE, trade_date DATE)"
        )
        con.execute("INSERT INTO daily_prices VALUES ('ABC', 12, 12, '2024-02-10')")
    finally:
        con.close()

    rows = holdings_at("2024-02-15", path=db_path, market_path=market_path)
    equity = next(row for row in rows if row["asset_type"] == "equity")
    option = next(row for row in rows if row["asset_type"] == "option")

    assert equity["market_price"] == 12.0
    assert equity["market_value"] == 120.0
    assert option["market_price"] == 5.0
    assert option["market_value"] == 5.0
    assert option["price_status"] == "stale_checkpoint"


def test_monthly_diff_preserves_cad_usd_identity_and_consumers_share_holdings(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        cad_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        usd_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="USD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=cad_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=usd_id,
            quantity=5,
            market_value=50,
            currency="USD",
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=cad_id,
            quantity=12,
            market_value=120,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=usd_id,
            quantity=4,
            market_value=40,
            currency="USD",
        )

    monkeypatch.setattr(holdings_service.sqlite_db, "SQLITE_PATH", db_path)
    monthly_diff = monthly_route.diff(
        a=date(2024, 1, 28), b=date(2024, 2, 28), account_id=None
    )
    february_holdings = holdings_at("2024-02-28", path=db_path)
    performance = _total_rows(path=db_path)
    performance_february = {
        row["currency"]: row["market_value"]
        for row in performance
        if row["as_of_date"] == "2024-02-28"
    }

    assert len(monthly_diff["rows"]) == 2
    assert {row["currency"] for row in monthly_diff["rows"]} == {"CAD", "USD"}
    assert len({row["holding_key"] for row in monthly_diff["rows"]}) == 2
    assert _held_symbols_at("2024-02-28", [], path=db_path) == ["ABC"]
    assert performance_february == {
        "CAD": sum(
            row["market_value"] or 0.0
            for row in february_holdings
            if row["currency"] == "CAD"
        ),
        "USD": sum(
            row["market_value"] or 0.0
            for row in february_holdings
            if row["currency"] == "USD"
        ),
    }


def test_composite_contributor_bundles_are_immutable_and_internally_consistent():
    states = _composite_states("cross_date_metadata_mixing", (10.0, 20.0))
    states[0].movement_source_refs.append(dict(states[0].movement_source_refs[0]))

    combined = holdings_service._combine_security_states(states)
    reversed_combined = holdings_service._combine_security_states(list(reversed(states)))

    assert combined.composite is True
    assert combined.quantity == 30.0
    assert combined.scope_key is None
    assert combined.anchor is None
    assert combined.source_snapshot_id is None
    assert combined.avg_cost is None
    assert combined.book_value is None
    assert combined.market_price is None
    assert combined.market_value is None
    assert combined.unrealized_pnl is None
    assert combined.contributors == reversed_combined.contributors
    assert combined.movement_source_refs == reversed_combined.movement_source_refs
    assert len(combined.movement_source_refs) == 2
    assert all(contributor.__dataclass_params__.frozen for contributor in combined.contributors)
    assert [contributor.scope_key for contributor in combined.contributors] == [
        "scope-0",
        "scope-1",
    ]
    assert [contributor.checkpoint_date for contributor in combined.contributors] == [
        "2024-01-31",
        "2024-02-28",
    ]
    assert [contributor.source_ref.row_id for contributor in combined.contributors] == [
        900,
        901,
    ]
    assert [contributor.source_ref.page_numbers for contributor in combined.contributors] == [
        (1,),
        (2,),
    ]

    combined.movement_source_refs.append({"kind": "transaction", "id": -1})
    assert all(
        {"kind": "transaction", "id": -1} not in state.movement_source_refs
        for state in states
    )
    assert holdings_service._combine_security_states([states[0]]) is states[0]


def test_composite_serialization_skips_singular_reconciliation_and_broker_facts():
    class ReconciliationLookupMustNotRun(dict):
        def get(self, key, default=None):
            raise AssertionError(f"unexpected aggregate reconciliation lookup: {key!r}")

    combined = holdings_service._combine_security_states(
        _composite_states("same_date_reported_leakage", (10.0, 20.0))
    )
    record = holdings_service._security_record(
        combined,
        as_of="2024-02-28",
        account={
            "account_number": "A-1",
            "nickname": None,
            "institution_code": "TST",
            "institution_name": "Test Broker",
        },
        reconciliation_results=ReconciliationLookupMustNotRun(),
    )

    assert record["holding_state"] == "incomplete"
    assert record["is_reported"] is False
    assert record["is_reconstructed"] is True
    assert record["scope_key"] is None
    assert record["source_ref"] is None
    assert record["checkpoint_date"] is None
    assert record["checkpoint_statement_id"] is None
    assert record["checkpoint_snapshot_set_id"] is None
    assert record["reconciliation_status"] is None
    assert record["reconciliation_reason"] is None
    assert record["provenance"]["type"] == "multiple_checkpoints"
    assert record["provenance"]["checkpoint"] is None
    assert len(record["provenance"]["checkpoints"]) == 2
    assert record["avg_cost"] is None
    assert record["book_value"] is None
    assert record["market_price"] is None
    assert record["market_value"] is None
    assert record["unrealized_pnl"] is None
    assert record["price_status"] == "unpriced"
    assert record["_anchor_market_price"] is None
    assert record["_anchor_market_value"] is None


def test_focused_composite_regression_examples_cover_known_leak_paths():
    expected_quantities = (10.0, 20.0)
    for scenario in _COMPOSITE_DEFECT_SCENARIOS:
        independent_price = (
            12.0
            if scenario in {"same_date_reported_leakage", "distinct_source_geometry"}
            else None
        )
        record = _serialize_composite(
            _composite_states(scenario, expected_quantities),
            as_of=_composite_as_of(scenario),
            independent_market_price=independent_price,
        )
        assert record["quantity"] == 30.0, scenario
        assert record["provenance"]["type"] == "multiple_checkpoints", scenario
        assert len(record["provenance"]["checkpoints"]) == 2, scenario
        assert record["source_ref"] is None, scenario
        assert record["checkpoint_date"] is None, scenario
        assert record["price_status"] == (
            "market" if independent_price is not None else "unpriced"
        ), scenario
        assert record["market_value"] == (
            independent_price * 30.0 if independent_price is not None else None
        ), scenario

    option_states = _composite_states("same_date_reported_leakage", expected_quantities)
    for state in option_states:
        state.asset_type = "option"
        state.option_expiry = "2024-06-21"
        state.option_strike = 100.0
        state.option_type = "CALL"
    option_record = _serialize_composite(
        option_states,
        as_of="2024-02-28",
        independent_market_price=12.0,
    )
    assert option_record["quantity"] == 30.0
    assert option_record["market_price"] is None
    assert option_record["market_value"] is None
    assert option_record["price_status"] == "unpriced"

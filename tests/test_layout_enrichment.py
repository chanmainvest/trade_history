"""Schema-v8 domains and replaceable source-geometry regressions."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ledger.api.app import app
from ledger.api.routes.statements import _persisted_boxes
from ledger.db import sqlite as sqlite_db
from ledger.domains import utc_now_text
from ledger.identity import canonical_evidence_key
from ledger.ingest.layout_enrichment import (
    _match_evidence,
    _StoredLine,
    _tokens,
    _write_source_geometry,
)
from ledger.parsers.layout import normalize_layout_text
from ledger.pdf_text import PdfLine, PdfText, PdfWord

from .db_fixtures import seed_source


def _geometry_pdf(sha256: str) -> PdfText:
    first = PdfLine(
        page_number=1,
        line_number=1,
        text="BUY ABC 10 12.00",
        x0=10,
        top=20,
        x1=110,
        bottom=30,
        words=(PdfWord("BUY", 10, 20, 30, 30), PdfWord("ABC", 35, 20, 60, 30)),
    )
    second = PdfLine(
        page_number=1,
        line_number=2,
        text="BUY ABC 10 12.00",
        x0=10,
        top=40,
        x1=110,
        bottom=50,
    )
    return PdfText(
        relpath="Statements/Test/layout.pdf",
        page_count=1,
        pages=["BUY ABC 10 12.00\nBUY ABC 10 12.00"],
        sha256=sha256,
        size_bytes=42,
        page_lines=[[first, second]],
        page_sizes=[(612.0, 792.0)],
    )


def _cash_geometry_pdf(sha256: str) -> PdfText:
    texts = ["Beginning cash balance $100.00", "Jan 10 Buy ABC 10 -20.00", "Ending cash balance $80.00"]
    lines = [
        PdfLine(
            page_number=1,
            line_number=index,
            text=value,
            x0=10,
            top=20.0 * index,
            x1=210,
            bottom=20.0 * index + 10,
        )
        for index, value in enumerate(texts, start=1)
    ]
    return PdfText(
        relpath="Statements/Test/cash-layout.pdf",
        page_count=1,
        pages=["\n".join(texts)],
        sha256=sha256,
        size_bytes=84,
        page_lines=[lines],
        page_sizes=[(612.0, 792.0)],
    )


def test_evidence_identity_does_not_change_when_geometry_changes():
    common = {
        "source_identity": "a" * 64,
        "row_kind": "transaction",
        "occurrence": 7,
        "raw_text": "BUY ABC 10 12.00",
        "parser_rule": "fixture:buy",
    }
    first = canonical_evidence_key(**common, page_number=1, line_number=2)
    second = canonical_evidence_key(**common, page_number=9, line_number=99)
    assert first == second
    assert first.startswith("ev2:")


def test_layout_enrichment_uses_persisted_hints_and_verify_uses_stored_boxes(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        source_id = seed_source(conn, "Statements/Test/layout.pdf")
        source = conn.execute(
            "SELECT sha256, active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_id,),
        ).fetchone()
        evidence_ids = [
            sqlite_db.upsert_source_evidence(
                conn,
                source_file_id=source_id,
                ingestion_run_id=int(source["active_ingestion_run_id"]),
                row_kind="transaction",
                occurrence=occurrence,
                raw_text="BUY ABC 10 12.00",
                parser_version="fixture",
                parser_rule="fixture:buy",
                page_number=1,
                line_number=occurrence,
            )
            for occurrence in (1, 2)
        ]
        evidence_rows = conn.execute(
            """
            SELECT evidence_id, row_kind, raw_text, page_number, line_number
              FROM source_evidence ORDER BY evidence_id
            """
        ).fetchall()
        metrics = _write_source_geometry(
            conn,
            source_file_id=source_id,
            ingestion_run_id=int(source["active_ingestion_run_id"]),
            source_sha256=str(source["sha256"]),
            pdf=_geometry_pdf(str(source["sha256"])),
            evidence_rows=evidence_rows,
        )

    assert metrics["exact"] == 2
    pages = _persisted_boxes(
        Path("unused-because-page-metadata-is-persisted.pdf"),
        source_file_id=source_id,
        references=[
            {
                "kind": "transaction",
                "id": 100 + index,
                "label": "buy ABC",
                "raw_line": "deliberately different display text",
                "evidence_id": evidence_id,
            }
            for index, evidence_id in enumerate(evidence_ids)
        ],
        path=db_path,
    )
    assert pages is not None
    assert pages[0]["width"] == 612.0
    assert [line["bbox"][1] for line in pages[0]["lines"]] == [20.0, 40.0]
    assert [line["refs"][0]["id"] for line in pages[0]["lines"]] == [100, 101]
    assert all(
        line["refs"][0]["match_method"] == "persisted_page_line"
        for line in pages[0]["lines"]
    )


def test_layout_enrichment_quarantines_repeated_text_without_a_unique_hint(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        source_id = seed_source(conn, "Statements/Test/ambiguous-layout.pdf")
        source = conn.execute(
            "SELECT sha256, active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_id,),
        ).fetchone()
        evidence_id = sqlite_db.upsert_source_evidence(
            conn,
            source_file_id=source_id,
            ingestion_run_id=int(source["active_ingestion_run_id"]),
            row_kind="transaction",
            occurrence=1,
            raw_text="BUY ABC 10 12.00",
            parser_version="fixture",
            parser_rule="fixture:buy",
        )
        evidence_rows = conn.execute(
            """
            SELECT evidence_id, row_kind, raw_text, page_number, line_number
              FROM source_evidence WHERE evidence_id = ?
            """,
            (evidence_id,),
        ).fetchall()
        metrics = _write_source_geometry(
            conn,
            source_file_id=source_id,
            ingestion_run_id=int(source["active_ingestion_run_id"]),
            source_sha256=str(source["sha256"]),
            pdf=_geometry_pdf(str(source["sha256"])),
            evidence_rows=evidence_rows,
        )
        geometry = conn.execute(
            """
            SELECT status, match_method FROM source_evidence_geometry
             WHERE evidence_id = ?
            """,
            (evidence_id,),
        ).fetchone()

    assert metrics["ambiguous"] == 1
    assert dict(geometry) == {
        "status": "ambiguous",
        "match_method": "repeated_exact_text",
    }


def test_layout_enrichment_links_ordered_noncontiguous_cash_lines(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        source_id = seed_source(conn, "Statements/Test/cash-layout.pdf")
        source = conn.execute(
            "SELECT sha256, active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_id,),
        ).fetchone()
        evidence_id = sqlite_db.upsert_source_evidence(
            conn,
            source_file_id=source_id,
            ingestion_run_id=int(source["active_ingestion_run_id"]),
            row_kind="cash",
            occurrence=1,
            raw_text="Beginning cash balance $100.00\nEnding cash balance $80.00",
            parser_version="fixture",
            parser_rule="fixture:cash",
            page_number=1,
        )
        evidence_rows = conn.execute(
            "SELECT evidence_id, row_kind, raw_text, page_number, line_number "
            "FROM source_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchall()

        metrics = _write_source_geometry(
            conn,
            source_file_id=source_id,
            ingestion_run_id=int(source["active_ingestion_run_id"]),
            source_sha256=str(source["sha256"]),
            pdf=_cash_geometry_pdf(str(source["sha256"])),
            evidence_rows=evidence_rows,
            allowed_pages_by_evidence={evidence_id: frozenset({1})},
        )
        geometry = conn.execute(
            "SELECT status, match_method FROM source_evidence_geometry WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        link_count = conn.execute(
            "SELECT COUNT(*) FROM source_evidence_lines WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()[0]

    assert metrics["exact"] == 1
    assert dict(geometry) == {
        "status": "exact",
        "match_method": "ordered_noncontiguous_lines",
    }
    assert link_count == 2


def test_layout_token_match_prefers_unique_narrowest_line_window():
    texts = [
        "BANK OF EXAMPLE 1,600 SEG 20.000 1.00 2.00 1.00 10.00%",
        "NUTRIENT EXAMPLE (NTR ) 1,000 SEG 94.410 70,015.00 94,410.00 24,395.00 4.41%",
    ]
    lines = [
        _StoredLine(
            source_line_id=index,
            line=PdfLine(
                page_number=1,
                line_number=index,
                text=value,
                x0=10,
                top=20 * index,
                x1=500,
                bottom=20 * index + 10,
            ),
            normalized=normalize_layout_text(value),
            tokens=_tokens(value),
            token_words=tuple(range(len(_tokens(value)))),
        )
        for index, value in enumerate(texts, start=1)
    ]

    match = _match_evidence(
        "NUTRIENT EXAMPLE (NTR) 1,000 SEG 94.410 70,015.00 94,410.00 24,395.00 4.41%",
        lines,
        page_hint=None,
        line_hint=None,
        allowed_pages=frozenset({1}),
    )

    assert match.status == "unique_tokens"
    assert match.line_indexes == (1,)


def test_layout_matches_one_semantic_cash_row_split_across_overlapping_lines():
    texts = [
        "Beginning cash balance 100.00",
        "$125.00",
        "May 31 Ending cash balance",
    ]
    tops = [10.0, 40.0, 41.0]
    lines = [
        _StoredLine(
            source_line_id=index,
            line=PdfLine(
                page_number=1,
                line_number=index,
                text=value,
                x0=10,
                top=tops[index - 1],
                x1=500,
                bottom=tops[index - 1] + 10,
            ),
            normalized=normalize_layout_text(value),
            tokens=_tokens(value),
            token_words=tuple(range(len(_tokens(value)))),
        )
        for index, value in enumerate(texts, start=1)
    ]

    match = _match_evidence(
        "Beginning cash balance 100.00\nMay 31 Ending cash balance $125.00",
        lines,
        page_hint=1,
        line_hint=1,
        allowed_pages=frozenset({1}),
    )

    assert match.status == "exact"
    assert match.method == "ordered_noncontiguous_lines"
    assert match.line_indexes == (0, 1, 2)


def test_schema_v9_rejects_invalid_domains_and_uses_canonical_utc(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_now_text())
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
        with pytest.raises(ValueError, match="unsupported ledger currency"):
            sqlite_db.upsert_account(
                conn,
                institution_id=institution_id,
                account_number="A-1",
                base_currency="EUR",
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO source_files(relpath, sha256) VALUES ('bad.pdf', 'not-a-hash')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO accounts(institution_id, account_number, base_currency, opened_on)
                VALUES (?, 'A-2', 'CAD', '01/02/2024')
                """,
                (institution_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO accounts(institution_id, account_number, base_currency, opened_on)
                VALUES (?, 'A-3', 'CAD', '2024-02-30')
                """,
                (institution_id,),
            )


def test_api_rejects_non_iso_calendar_dates_before_querying_the_ledger():
    client = TestClient(app)
    assert client.get("/transactions", params={"start": "not-a-date"}).status_code == 422
    assert client.get("/monthly/diff", params={"a": "2024-02-30", "b": "2024-03-01"}).status_code == 422

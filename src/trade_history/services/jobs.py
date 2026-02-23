from __future__ import annotations

from pathlib import Path
from typing import Any

from trade_history.config import settings
from trade_history.core.positions import rebuild_positions
from trade_history.db.sqlite import db_session, init_db
from trade_history.ingest.fx import ingest_boc_fx
from trade_history.ingest.market import ingest_prices
from trade_history.ingest.statements import ingest_statements


def run_statement_ingest(
    root: Path | str | None = None,
    institutions: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root_path = Path(root) if isinstance(root, str) else root
    report = ingest_statements(root=root_path or settings.statements_root, institutions=institutions, force=force)
    with db_session() as conn:
        position_report = rebuild_positions(conn)
    return {"statements": report.to_dict(), "positions": position_report}


def run_price_ingest(
    use_stooq: bool = True,
    use_yahoo: bool = True,
    refresh_sector_metadata: bool = True,
) -> dict[str, Any]:
    report = ingest_prices(
        use_stooq=use_stooq,
        use_yahoo=use_yahoo,
        refresh_sector_metadata=refresh_sector_metadata,
    )
    return report.to_dict()


def run_fx_ingest() -> dict[str, Any]:
    report = ingest_boc_fx()
    return report.to_dict()


def rebuild_views() -> dict[str, Any]:
    init_db()
    with db_session() as conn:
        return rebuild_positions(conn)

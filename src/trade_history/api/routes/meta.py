from __future__ import annotations

from fastapi import APIRouter, Depends

from trade_history.api.deps import get_current_auth_context, require_read_access
from trade_history.db.sqlite import get_connection, init_db


router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/accounts")
def get_accounts(
    _=Depends(require_read_access),
) -> dict:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT account_id, institution, account_name, account_type, base_currency, masked_number
            FROM accounts
            ORDER BY institution, account_id
            """
        ).fetchall()
        return {"items": [dict(row) for row in rows]}


@router.get("/auth-context")
def get_auth_context(
    context=Depends(get_current_auth_context),
) -> dict:
    return {
        "subject": context.subject,
        "provider": context.provider,
        "scopes": context.scopes,
    }

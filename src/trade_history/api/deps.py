from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from trade_history.api.auth import AuthContext, LocalAuthProvider, get_auth_provider
from trade_history.config import settings


bearer = HTTPBearer(auto_error=False)


def get_current_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthContext:
    try:
        provider = get_auth_provider()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    if isinstance(provider, LocalAuthProvider):
        return provider.validate_token(credentials.credentials if credentials else None)

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    context = provider.validate_token(credentials.credentials)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
    return context


def _has_scope(context: AuthContext, scope: str) -> bool:
    if settings.auth_mode == "none":
        return True
    if scope in context.scopes:
        return True
    # Also accept wildcard-ish scopes if the IdP provides broad grants.
    if "trade_history.*" in context.scopes or "*" in context.scopes:
        return True
    return False


def require_scope(scope: str) -> Callable[[AuthContext], AuthContext]:
    def dependency(context: AuthContext = Depends(get_current_auth_context)) -> AuthContext:
        if _has_scope(context, scope):
            return context
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required scope: {scope}",
        )

    return dependency


def require_read_access(context: AuthContext = Depends(get_current_auth_context)) -> AuthContext:
    if _has_scope(context, settings.auth_read_scope):
        return context
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing required scope: {settings.auth_read_scope}",
    )


def require_write_access(context: AuthContext = Depends(get_current_auth_context)) -> AuthContext:
    if _has_scope(context, settings.auth_write_scope):
        return context
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing required scope: {settings.auth_write_scope}",
    )

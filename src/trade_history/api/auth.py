from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from typing import Protocol

import jwt

from trade_history.config import settings


@dataclass(slots=True)
class AuthContext:
    subject: str
    provider: str
    scopes: list[str]


class AuthProvider(Protocol):
    def validate_token(self, token: str) -> AuthContext | None:
        ...


class LocalAuthProvider:
    """Local development provider. Always grants local scopes."""

    def validate_token(self, token: str | None = None) -> AuthContext:
        _ = token
        return AuthContext(subject="local-user", provider="none", scopes=["read", "write"])


class OAuthJwksProvider:
    def __init__(
        self,
        jwks_url: str,
        issuer: str | None = None,
        audience: str | None = None,
        algorithms: list[str] | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.algorithms = algorithms or ["RS256"]
        self._jwks = jwt.PyJWKClient(jwks_url)

    def validate_token(self, token: str) -> AuthContext | None:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": self.audience is not None,
                    "verify_iss": self.issuer is not None,
                },
            )
        except Exception:
            return None

        subject = str(payload.get("sub") or payload.get("client_id") or "oauth-user")
        scopes = _extract_scopes(payload)
        return AuthContext(subject=subject, provider="oauth", scopes=scopes)


def _extract_scopes(payload: dict[str, Any]) -> list[str]:
    scopes: list[str] = []
    scope_field = payload.get("scope")
    if isinstance(scope_field, str):
        scopes.extend([s for s in scope_field.split() if s.strip()])
    scp_field = payload.get("scp")
    if isinstance(scp_field, list):
        scopes.extend([str(s) for s in scp_field])
    roles_field = payload.get("roles")
    if isinstance(roles_field, list):
        scopes.extend([str(s) for s in roles_field])
    deduped: list[str] = []
    seen = set()
    for scope in scopes:
        if scope in seen:
            continue
        seen.add(scope)
        deduped.append(scope)
    return deduped


@lru_cache(maxsize=1)
def get_auth_provider() -> AuthProvider | LocalAuthProvider:
    if settings.auth_mode == "oauth":
        if not settings.auth_oauth_jwks_url:
            raise RuntimeError("TH_AUTH_MODE=oauth requires TH_AUTH_OAUTH_JWKS_URL")
        algorithms = [a.strip() for a in settings.auth_oauth_algorithms.split(",") if a.strip()]
        return OAuthJwksProvider(
            jwks_url=settings.auth_oauth_jwks_url,
            issuer=settings.auth_oauth_issuer,
            audience=settings.auth_oauth_audience,
            algorithms=algorithms or ["RS256"],
        )
    return LocalAuthProvider()

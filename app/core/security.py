"""Supabase JWT verification. We never store passwords; Supabase Auth is the IdP.

HS256 projects: verify with SUPABASE_JWT_SECRET.
Asymmetric projects (RS256/ES256/EdDSA): verify with keys from SUPABASE_JWKS_URL.
The algorithm is chosen from the token header but only from an allow-list that matches
what is configured, so "alg" can never downgrade verification.
"""

import asyncio
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import UnauthorizedError

ASYMMETRIC_ALGS = ["RS256", "ES256", "EdDSA"]


class TokenClaims(BaseModel):
    sub: UUID
    email: str | None = None
    role: str | None = None


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=3600)


def _decode(token: str, settings: Settings) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    options = {"require": ["exp", "sub"]}
    kwargs: dict[str, Any] = {"audience": settings.supabase_jwt_audience, "options": options}
    if settings.supabase_jwt_issuer:
        kwargs["issuer"] = settings.supabase_jwt_issuer

    if alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidTokenError("HS256 verification is not configured")
        return jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], **kwargs)
    if alg in ASYMMETRIC_ALGS:
        if not settings.supabase_jwks_url:
            raise jwt.InvalidTokenError("JWKS verification is not configured")
        signing_key = _jwks_client(settings.supabase_jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=[alg], **kwargs)
    raise jwt.InvalidTokenError(f"Unsupported algorithm: {alg}")


async def verify_token(token: str, settings: Settings) -> TokenClaims:
    try:
        # PyJWKClient does blocking network IO on cache miss, so keep it off the event loop.
        payload = await asyncio.to_thread(_decode, token, settings)
        return TokenClaims.model_validate(payload)
    except (jwt.PyJWTError, ValueError) as exc:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token") from exc

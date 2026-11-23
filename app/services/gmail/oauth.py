"""Google OAuth 2.0 (authorization-code flow, offline access) with plain httpx; no Google SDK."""

import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt

from app.core.config import Settings
from app.core.errors import AppError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  (public endpoint, not a secret)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
STATE_TTL_SECONDS = 600
_STATE_PURPOSE = "gmail_oauth"


class GmailNotConfigured(AppError):
    status_code = 503
    code = "gmail_not_configured"


class InvalidState(AppError):
    status_code = 400
    code = "invalid_oauth_state"


class OAuthError(AppError):
    status_code = 400
    code = "oauth_error"


class InvalidGrant(Exception):
    """The refresh token was revoked/expired: the user must reconnect."""


@dataclass(frozen=True, slots=True)
class TokenResponse:
    access_token: str
    refresh_token: str | None
    scope: str
    expires_in: int


def require_configured(settings: Settings) -> None:
    if not (settings.google_client_id and settings.google_client_secret and settings.google_redirect_uri):
        raise GmailNotConfigured("Gmail integration is not configured (GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI)")
    if not settings.state_secret:
        raise GmailNotConfigured("OAUTH_STATE_SECRET (or FERNET_KEYS) is required to sign OAuth state")


def make_state(settings: Settings, user_id: UUID, now: float | None = None) -> str:
    t = int(now if now is not None else time.time())
    claims = {
        "sub": str(user_id),
        "purpose": _STATE_PURPOSE,
        "nonce": secrets.token_urlsafe(8),
        "iat": t,
        "exp": t + STATE_TTL_SECONDS,
    }
    return jwt.encode(claims, settings.state_secret, algorithm="HS256")


def read_state(settings: Settings, state: str) -> UUID:
    try:
        claims = jwt.decode(
            state, settings.state_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
        if claims.get("purpose") != _STATE_PURPOSE:
            raise InvalidState("Wrong state purpose")
        return UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidState("OAuth state is invalid or expired; start the connection again") from exc


def build_authorization_url(settings: Settings, state: str) -> str:
    require_configured(settings)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GMAIL_READONLY,
        "access_type": "offline",  # we need a refresh token for the background sync
        "prompt": "consent",  # Google only returns a refresh token on consent
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def _post_token(http: httpx.AsyncClient, data: dict[str, str]) -> dict[str, object]:
    resp = await http.post(TOKEN_URL, data=data)
    body: dict[str, object] = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        if body.get("error") == "invalid_grant":
            raise InvalidGrant(str(body.get("error_description", "invalid_grant")))
        raise OAuthError(f"Google token endpoint error: {body.get('error', resp.status_code)}")
    return body


async def exchange_code(http: httpx.AsyncClient, settings: Settings, code: str) -> TokenResponse:
    require_configured(settings)
    try:
        body = await _post_token(
            http,
            {
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    except InvalidGrant as exc:
        raise OAuthError("Authorization code is invalid or already used") from exc
    return TokenResponse(
        access_token=str(body["access_token"]),
        refresh_token=str(body["refresh_token"]) if body.get("refresh_token") else None,
        scope=str(body.get("scope", "")),
        expires_in=int(str(body.get("expires_in", 3600))),
    )


async def refresh_access_token(http: httpx.AsyncClient, settings: Settings, refresh_token: str) -> str:
    require_configured(settings)
    body = await _post_token(
        http,
        {
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
    )
    return str(body["access_token"])


async def revoke_token(http: httpx.AsyncClient, token: str) -> bool:
    """Best effort: returns False when Google could not be reached or refused."""
    try:
        resp = await http.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        return False
    return resp.status_code == 200

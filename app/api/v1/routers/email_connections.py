from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, EncryptorDep, QueueDep, SessionDep, SettingsDep
from app.api.utils import get_owned
from app.core.errors import AppError
from app.models.integration import EmailConnection
from app.schemas.email_connection import EmailConnectionOut, GmailStartOut
from app.services.audit import record_audit
from app.services.gmail.client import GmailClient
from app.services.gmail.oauth import (
    GMAIL_READONLY,
    OAuthError,
    build_authorization_url,
    exchange_code,
    make_state,
    read_state,
    revoke_token,
)

router = APIRouter(prefix="/email-connections", tags=["email-connections"])
log = structlog.get_logger(__name__)


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Outbound HTTP for Google calls; tests override this with a mock transport."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        yield client


HttpDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


@router.post("/gmail/start", response_model=GmailStartOut)
async def gmail_start(user: CurrentUser, settings: SettingsDep) -> GmailStartOut:
    """Returns the Google consent URL (scope gmail.readonly, offline). The app opens it in a browser."""
    url = build_authorization_url(settings, make_state(settings, user.id))
    return GmailStartOut(authorization_url=url)


_PAGE = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Pulse</title><body style='font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;"
    "padding:0 1.25rem;line-height:1.5;color:#1d2b27'><h2>{title}</h2><p>{body}</p>"
)


def _finish(
    settings_redirect: str,
    payload: dict[str, str],
    status_code: int = 200,
    *,
    browser: bool = False,
) -> Response:
    if settings_redirect:
        sep = "&" if "?" in settings_redirect else "?"
        return RedirectResponse(f"{settings_redirect}{sep}{urlencode(payload)}", status_code=302)
    if browser:  # no app deep link configured: show a plain page instead of raw JSON
        ok = payload.get("status") == "connected"
        title, body = (
            (
                "Gmail connected",
                "You can close this tab and go back to Pulse. Your statements will appear shortly.",
            )
            if ok
            else ("Gmail was not connected", "Nothing was changed. Go back to Pulse and try again.")
        )
        return HTMLResponse(_PAGE.format(title=title, body=body), status_code=status_code)
    return JSONResponse(payload, status_code=status_code)


@router.get("/gmail/callback", include_in_schema=True)
async def gmail_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
    http: HttpDep,
    enc: EncryptorDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> Response:
    """Google redirects the browser here. Unauthenticated: the signed `state` identifies the user."""
    redirect = settings.oauth_success_redirect
    browser = "text/html" in request.headers.get("accept", "")
    if error:  # e.g. access_denied when the user cancels the consent screen
        return _finish(redirect, {"status": "denied", "reason": error[:100]}, 400, browser=browser)
    if not code or not state:
        raise AppError("Missing code or state", code="oauth_error")
    user_id = read_state(settings, state)
    try:
        tokens = await exchange_code(http, settings, code)
        if not tokens.refresh_token:
            raise OAuthError(
                "Google returned no refresh token; revoke the app in your Google account and retry"
            )
        if GMAIL_READONLY not in tokens.scope.split():
            raise OAuthError("The gmail.readonly permission was not granted")
        email = (await GmailClient(http, tokens.access_token).profile_email()).lower()
    except AppError as exc:
        if redirect or browser:
            return _finish(redirect, {"status": "error", "code": exc.code}, 400, browser=browser)
        raise

    conn = (
        await session.execute(
            select(EmailConnection).where(
                EmailConnection.user_id == user_id,
                EmailConnection.provider == "gmail",
                EmailConnection.email == email,
                EmailConnection.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    encrypted = enc.encrypt(tokens.refresh_token)
    if conn is None:
        conn = EmailConnection(
            user_id=user_id,
            provider="gmail",
            email=email,
            encrypted_refresh_token=encrypted,
            scopes=tokens.scope,
        )
        session.add(conn)
    else:
        conn.encrypted_refresh_token, conn.scopes = encrypted, tokens.scope
        conn.status, conn.last_error = "active", None
    await session.flush()
    record_audit(
        session,
        user_id=user_id,
        action="email_connection.connect",
        entity_type="email_connection",
        entity_id=conn.id,
        data={"provider": "gmail"},
    )
    await session.commit()
    try:
        await queue.enqueue_job(
            "sync_gmail_connection", str(conn.id), _job_id=f"gmail-sync:{conn.id}:{uuid4().hex[:8]}"
        )
    except Exception:
        log.exception("gmail_sync_enqueue_failed", connection_id=str(conn.id))
    return _finish(
        redirect, {"status": "connected", "email": email, "connection_id": str(conn.id)}, browser=browser
    )


@router.get("", response_model=list[EmailConnectionOut])
async def list_connections(user: CurrentUser, session: SessionDep) -> list[EmailConnection]:
    rows = await session.execute(
        select(EmailConnection)
        .where(EmailConnection.user_id == user.id, EmailConnection.deleted_at.is_(None))
        .order_by(EmailConnection.created_at)
    )
    return list(rows.scalars())


@router.post("/{connection_id}/sync", status_code=202)
async def sync_now(
    connection_id: UUID, user: CurrentUser, session: SessionDep, queue: QueueDep
) -> dict[str, str]:
    conn = await get_owned(session, EmailConnection, connection_id, user.id)
    await queue.enqueue_job(
        "sync_gmail_connection", str(conn.id), _job_id=f"gmail-sync:{conn.id}:{uuid4().hex[:8]}"
    )
    return {"status": "queued"}


@router.delete("/{connection_id}", status_code=204)
async def disconnect(
    connection_id: UUID, user: CurrentUser, session: SessionDep, http: HttpDep, enc: EncryptorDep
) -> Response:
    """Soft-delete and revoke the Google grant (best effort). Imported statements are kept."""
    from datetime import UTC, datetime

    conn = await get_owned(session, EmailConnection, connection_id, user.id)
    try:
        await revoke_token(http, enc.decrypt(conn.encrypted_refresh_token))
    except Exception:
        log.warning("gmail_revoke_failed", connection_id=str(conn.id))
    conn.deleted_at = datetime.now(UTC)
    record_audit(
        session,
        user_id=user.id,
        action="email_connection.disconnect",
        entity_type="email_connection",
        entity_id=conn.id,
    )
    await session.commit()
    return Response(status_code=204)

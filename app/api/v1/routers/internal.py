"""Callbacks for Cloud Tasks / Cloud Scheduler (QUEUE_BACKEND=cloudtasks). Not part of the public API."""

from typing import Any

import jwt
import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from app.core.cloudtasks import verify_google_oidc
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.queue import get_queue
from app.services.storage import get_storage
from app.workers import tasks

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

TASKS = {
    "process_ingest": tasks.process_ingest,
    "process_statement": tasks.process_statement,
    "sync_gmail_connection": tasks.sync_gmail_connection,
}
CRONS = {
    "sync_all_gmail_connections": tasks.sync_all_gmail_connections,
    "snapshot_net_worth": tasks.snapshot_net_worth,
    "refresh_valuations": tasks.refresh_valuations,
}


def _authorize(authorization: str | None) -> None:
    settings = get_settings()
    if settings.queue_backend != "cloudtasks" or not settings.tasks_invoker_sa or not settings.service_url:
        raise HTTPException(404)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401)
    try:
        verify_google_oidc(authorization[7:], settings)
    except (jwt.PyJWTError, ValueError, OSError) as exc:
        log.warning("internal_auth_failed", error=type(exc).__name__)
        raise HTTPException(401) from exc


async def _ctx() -> dict[str, Any]:
    return {"session_factory": get_sessionmaker(), "storage": get_storage(), "redis": await get_queue()}


@router.post("/tasks/{name}")
async def run_task(
    name: str, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    _authorize(authorization)
    fn = TASKS.get(name)
    if fn is None:
        raise HTTPException(404)
    args = (await request.json()).get("args", [])
    # A raised error becomes a 500, which makes Cloud Tasks retry per the queue's retry config.
    return {"result": await fn(await _ctx(), *args)}


@router.post("/cron/{name}")
async def run_cron(name: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize(authorization)
    fn = CRONS.get(name)
    if fn is None:
        raise HTTPException(404)
    return {"result": await fn(await _ctx())}

import asyncio

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.queue import QueuePool, get_queue

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. No dependencies are touched."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Readiness: database and Redis reachable."""
    checks: dict[str, str] = {}
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=3)
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "fail"
    try:
        queue: QueuePool = await asyncio.wait_for(get_queue(), timeout=3)
        await asyncio.wait_for(queue.ping(), timeout=3)
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "fail"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "unavailable", "checks": checks}

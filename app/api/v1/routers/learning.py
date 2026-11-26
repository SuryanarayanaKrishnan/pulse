"""Shared merchant learning (docs/LEARNING.md in the app repo): phones that opted in send anonymous
"merchant -> category" confirmations; we keep weekly sums only. A reviewed map is served back to everyone.

Anonymous by design (no auth, no ids). Batches that break the rules are rejected, requests are capped, and
nothing is promoted to the served map automatically: a human reviews the aggregates first.
"""

import json
import re
import time
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import SessionDep
from app.models.learning import CategoryContribution

router = APIRouter(tags=["learning"])

MAP_PATH = Path(__file__).resolve().parents[4] / "data" / "learning" / "merchant_map.json"
MAX_PAIRS = 200
PER_IP_PER_HOUR = 20
_ip_hits: dict[str, deque[float]] = defaultdict(deque)

# Categories that may be learned. People- and account-like ones never are (salary, family, savings, ...).
ALLOWED_CATEGORIES = frozenset(
    {
        "dining",
        "education",
        "entertainment",
        "fees",
        "fixed_expenses",
        "groceries",
        "health",
        "shopping",
        "telecom",
        "transport",
        "travel",
        "utilities",
    }
)
_KEY = re.compile(r"^[a-z][a-z' &.-]{1,38}[a-z]$")
_BLOCKED = re.compile(
    r"@|https?|www|\+|\d|transfer|iban|salary|payroll|atm|cheque|\b(mr|mrs|ms|bin|abu)\b",
)


class Pair(BaseModel):
    k: str = Field(max_length=40)
    c: str = Field(max_length=32)
    n: int = Field(ge=1, le=3)


class Batch(BaseModel):
    format: int
    map_version: int = 0
    pairs: list[Pair] = Field(max_length=MAX_PAIRS)


def _valid(p: Pair) -> bool:
    return (
        p.c in ALLOWED_CATEGORIES
        and bool(_KEY.match(p.k))
        and len(p.k.split()) <= 5
        and not _BLOCKED.search(p.k)
    )


def _allow(ip: str, now: float) -> None:
    hits = _ip_hits[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= PER_IP_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many requests")
    hits.append(now)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


@router.post("/category-contributions", status_code=204)
async def contribute(batch: Batch, request: Request, session: SessionDep) -> Response:
    _allow(request.client.host if request.client else "unknown", time.time())
    if batch.format != 1:
        raise HTTPException(status_code=422, detail="unsupported format")
    if not batch.pairs or not all(_valid(p) for p in batch.pairs):
        raise HTTPException(status_code=422, detail="batch rejected")
    week = week_start(datetime.now(UTC).date())
    for p in batch.pairs:
        stmt = insert(CategoryContribution).values(week=week, merchant_key=p.k, category=p.c, n=p.n)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_category_contrib",
            set_={"n": CategoryContribution.n + p.n, "updated_at": datetime.now(UTC)},
        )
        await session.execute(stmt)
    await session.commit()
    return Response(status_code=204)


@router.get("/merchant-map")
async def merchant_map(response: Response) -> dict[str, Any]:
    """The reviewed shared map (same shape as the app's bundled assets/merchant_map.json). Public."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    data: dict[str, Any] = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return data

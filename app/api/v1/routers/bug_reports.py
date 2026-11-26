"""Beta bug reports: the app posts a redacted report, we file a GitHub issue. The token stays on the server.

Contract (docs/BUG_REPORTS.md in the app repo): POST JSON {title, category, body, device?,
screenshot_png_base64?}
-> 201 {"issue_url": "..."}. Anonymous by design (testers may not be signed in), so it is capped: a body size
limit, a per-IP hourly limit and a global daily limit (in memory; they reset on restart).
"""

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import SettingsDep

router = APIRouter(prefix="/bug-reports", tags=["bug-reports"])

PER_IP_PER_HOUR = 5
GLOBAL_PER_DAY = 200
MAX_BYTES = 1_000_000

_ip_hits: dict[str, deque[float]] = defaultdict(deque)
_day_hits: deque[float] = deque()


async def get_issue_client() -> AsyncIterator[httpx.AsyncClient]:
    """Outbound HTTP to GitHub; tests override this with a mock transport."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        yield client


class BugReport(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: Literal["bug", "confusing", "idea"]
    body: str = Field(max_length=60_000)
    device: dict[str, Any] | None = None
    screenshot_png_base64: str | None = Field(default=None, max_length=1_000_000)


def _allow(ip: str, now: float) -> None:
    hits = _ip_hits[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    while _day_hits and now - _day_hits[0] > 86400:
        _day_hits.popleft()
    if len(hits) >= PER_IP_PER_HOUR or len(_day_hits) >= GLOBAL_PER_DAY:
        raise HTTPException(status_code=429, detail="Too many reports, try again later")
    hits.append(now)
    _day_hits.append(now)


@router.post("", status_code=201)
async def file_bug_report(
    report: BugReport,
    request: Request,
    settings: SettingsDep,
    client: Annotated[httpx.AsyncClient, Depends(get_issue_client)],
) -> dict[str, str]:
    if not (settings.bug_report_github_token and settings.bug_report_repo):
        raise HTTPException(status_code=503, detail="Bug reports are not switched on")
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Report too large")
    _allow(request.client.host if request.client else "unknown", time.time())

    repo = settings.bug_report_repo
    headers = {
        "Authorization": f"Bearer {settings.bug_report_github_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pulse-api",
    }
    body = report.body
    if report.screenshot_png_base64:
        name = f"screens/{int(time.time())}-{uuid4()}.png"
        put = await client.put(
            f"https://api.github.com/repos/{repo}/contents/{name}",
            headers=headers,
            json={"message": "bug screenshot", "content": report.screenshot_png_base64},
        )
        if put.is_success:
            body += f"\n\n## Screenshot\nhttps://github.com/{repo}/blob/HEAD/{name}"
    res = await client.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers=headers,
        json={"title": report.title, "body": body, "labels": ["beta-report", report.category]},
    )
    if not res.is_success:
        raise HTTPException(status_code=502, detail="Could not file the report")
    return {"issue_url": str(res.json().get("html_url", ""))}

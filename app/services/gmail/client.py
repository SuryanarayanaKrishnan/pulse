"""Minimal Gmail REST client (users.messages.list/get, attachments.get, profile)."""

import asyncio
import base64
from collections.abc import Iterator
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class GmailApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Gmail API {status}: {message}")
        self.status = status


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    mime_type: str
    attachment_id: str
    size: int
    index: int  # stable position among the message's attachments (used in idempotency keys)


class GmailClient:
    def __init__(
        self, http: httpx.AsyncClient, access_token: str, *, retries: int = 2, backoff: float = 0.5
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._retries = retries
        self._backoff = backoff

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(self._retries + 1):
            resp = await self._http.get(f"{API}{path}", params=params, headers=self._headers)
            if resp.status_code in _RETRY_STATUSES and attempt < self._retries:
                await asyncio.sleep(self._backoff * (2**attempt))
                continue
            if resp.status_code >= 400:
                raise GmailApiError(resp.status_code, resp.text[:200])
            body: dict[str, Any] = resp.json()
            return body
        raise AssertionError("unreachable")  # pragma: no cover

    async def profile_email(self) -> str:
        return str((await self._get("/profile"))["emailAddress"])

    async def search(self, query: str, *, limit: int = 100) -> list[str]:
        """Message ids matching ``query`` (newest first), following pagination up to ``limit``."""
        ids: list[str] = []
        page: str | None = None
        while len(ids) < limit:
            params: dict[str, Any] = {"q": query, "maxResults": min(100, limit - len(ids))}
            if page:
                params["pageToken"] = page
            body = await self._get("/messages", params)
            ids.extend(m["id"] for m in body.get("messages", []))
            page = body.get("nextPageToken")
            if not page:
                break
        return ids[:limit]

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await self._get(f"/messages/{message_id}", {"format": "full"})

    async def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        body = await self._get(f"/messages/{message_id}/attachments/{attachment_id}")
        return base64.urlsafe_b64decode(str(body.get("data", "")) + "===")


def header(message: dict[str, Any], name: str) -> str | None:
    for h in message.get("payload", {}).get("headers", []):
        if str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value"))
    return None


def sender_address(message: dict[str, Any]) -> str | None:
    value = header(message, "From")
    addr = parseaddr(value)[1] if value else ""
    return addr.lower() or None


def pdf_attachments(message: dict[str, Any], max_bytes: int) -> Iterator[Attachment]:
    """Walk the MIME tree and yield PDF attachments (skips oversized ones)."""
    index = 0

    def walk(part: dict[str, Any]) -> Iterator[Attachment]:
        nonlocal index
        for sub in part.get("parts", []) or []:
            yield from walk(sub)
        body = part.get("body", {})
        filename = part.get("filename") or ""
        mime = str(part.get("mimeType", ""))
        if body.get("attachmentId") and (mime == "application/pdf" or filename.lower().endswith(".pdf")):
            size = int(body.get("size", 0))
            att = Attachment(filename or "statement.pdf", mime, str(body["attachmentId"]), size, index)
            index += 1
            if size <= max_bytes:
                yield att

    yield from walk(message.get("payload", {}))

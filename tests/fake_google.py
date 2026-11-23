"""In-memory Google (OAuth token endpoint + Gmail REST) behind an httpx MockTransport."""

import base64
import json
from typing import Any
from urllib.parse import parse_qs

import httpx

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class FakeGoogle:
    def __init__(self) -> None:
        self.refresh_token: str | None = "rt-1"
        self.scope = "https://www.googleapis.com/auth/gmail.readonly"
        self.email = "Me@Example.com"
        self.invalid_grant = False
        self.messages: dict[str, dict[str, Any]] = {}
        self.attachments: dict[tuple[str, str], bytes] = {}
        self.queries: list[str] = []
        self.attachment_fetches = 0
        self.revoked: list[str] = []
        self.flaky_search = 0  # number of 503s before search succeeds
        self.codes_used: set[str] = set()
        self.leaky: set[str] = (
            set()
        )  # message ids returned by every search (as if the sender filter missed them)

    def add_email(
        self,
        message_id: str,
        sender: str,
        pdf: bytes | None,
        *,
        subject: str = "Your e-statement",
        filename: str = "Statement.pdf",
        extra_parts: list[dict[str, Any]] | None = None,
    ) -> None:
        parts: list[dict[str, Any]] = [
            {"mimeType": "text/plain", "filename": "", "body": {"size": 5, "data": b64(b"hello")}}
        ]
        if pdf is not None:
            att_id = f"att-{message_id}"
            self.attachments[(message_id, att_id)] = pdf
            parts.append(
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": filename,
                    "body": {"attachmentId": att_id, "size": len(pdf)},
                }
            )
        parts.extend(extra_parts or [])
        self.messages[message_id] = {
            "id": message_id,
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
                "parts": [{"mimeType": "multipart/alternative", "parts": parts[:1]}, *parts[1:]],
            },
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form["grant_type"] == "authorization_code":
                if form["code"] in self.codes_used or form["code"] == "bad-code":
                    return httpx.Response(
                        400, json={"error": "invalid_grant", "error_description": "Bad Request"}
                    )
                self.codes_used.add(form["code"])
                body: dict[str, Any] = {
                    "access_token": "at-1",
                    "expires_in": 3599,
                    "scope": self.scope,
                    "token_type": "Bearer",
                }
                if self.refresh_token:
                    body["refresh_token"] = self.refresh_token
                return httpx.Response(200, json=body)
            if self.invalid_grant or form["refresh_token"] != self.refresh_token:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "Token has been expired or revoked.",
                    },
                )
            return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3599})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200, json={})
        if not url.startswith(GMAIL):
            return httpx.Response(404, json={"error": "unexpected " + url})
        if request.headers.get("authorization") not in ("Bearer at-1", "Bearer at-2"):
            return httpx.Response(401, json={"error": "unauthenticated"})
        rel = path.split("/users/me", 1)[1]
        if rel == "/profile":
            return httpx.Response(200, json={"emailAddress": self.email})
        if rel == "/messages":
            if self.flaky_search > 0:
                self.flaky_search -= 1
                return httpx.Response(503, json={"error": "backend"})
            q = request.url.params.get("q", "")
            self.queries.append(q)
            ids = [
                mid
                for mid, m in self.messages.items()
                if "has:attachment" in q and (mid in self.leaky or any(f"from:{d}" in q for d in _domains(m)))
            ]
            return httpx.Response(
                200, json={"messages": [{"id": i} for i in ids]} if ids else {"resultSizeEstimate": 0}
            )
        if rel.startswith("/messages/") and "/attachments/" in rel:
            _, _, mid, _, aid = rel.split("/")
            self.attachment_fetches += 1
            return httpx.Response(200, json={"size": 1, "data": b64(self.attachments[(mid, aid)])})
        if rel.startswith("/messages/"):
            mid = rel.rsplit("/", 1)[1]
            return (
                httpx.Response(200, json=self.messages[mid])
                if mid in self.messages
                else httpx.Response(404, json={})
            )
        return httpx.Response(404, json={})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _domains(message: dict[str, Any]) -> list[str]:
    frm = next(h["value"] for h in message["payload"]["headers"] if h["name"] == "From")
    addr = frm.rsplit("<", 1)[-1].strip(" >")
    domain = addr.rsplit("@", 1)[1]
    # a real Gmail `from:emiratesnbd.com` matches the domain and its subdomains
    parts = domain.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def dumps(obj: Any) -> str:
    return json.dumps(obj)

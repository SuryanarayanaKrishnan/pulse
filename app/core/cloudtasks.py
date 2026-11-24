"""Cloud Tasks queue (REST, no extra dependencies) and OIDC verification for /internal/* callbacks."""

import base64
import json
import re
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import Settings

_METADATA_HOST = "http://metadata.google.internal/computeMetadata/v1"
_METADATA_TOKEN_URL = f"{_METADATA_HOST}/instance/service-accounts/default/token"
_GOOGLE_CERTS = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


class CloudTasksQueue:
    """Implements the `QueuePool` protocol on top of Cloud Tasks HTTP targets.

    Each task POSTs `{"args": [...]}` to `{service_url}/internal/tasks/{function}` with an OIDC token.
    `_job_id` becomes the task name, so enqueueing the same id twice is a no-op (like arq).
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._s = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._token: tuple[str, float] | None = None

    async def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = await self._client.get(_METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        r.raise_for_status()
        data = r.json()
        self._token = (data["access_token"], time.time() + int(data.get("expires_in", 300)))
        return self._token[0]

    @property
    def _queue_path(self) -> str:
        s = self._s
        return f"projects/{s.gcp_project}/locations/{s.gcp_location}/queues/{s.cloud_tasks_queue}"

    async def enqueue_job(self, function: str, *args: Any, _job_id: str | None = None, **_: Any) -> Any:
        s = self._s
        task: dict[str, Any] = {
            "dispatchDeadline": "300s",
            "httpRequest": {
                "httpMethod": "POST",
                "url": f"{s.service_url.rstrip('/')}/internal/tasks/{function}",
                "headers": {"Content-Type": "application/json"},
                "body": base64.b64encode(json.dumps({"args": list(args)}).encode()).decode(),
                "oidcToken": {
                    "serviceAccountEmail": s.tasks_invoker_sa,
                    "audience": s.service_url.rstrip("/"),
                },
            },
        }
        if _job_id:
            task["name"] = f"{self._queue_path}/tasks/{_NAME_RE.sub('-', _job_id)[:480]}"
        r = await self._client.post(
            f"https://cloudtasks.googleapis.com/v2/{self._queue_path}/tasks",
            headers={"Authorization": f"Bearer {await self._access_token()}"},
            json={"task": task},
        )
        if r.status_code == 409:  # same task name already queued/recently run: deduplicated
            return None
        r.raise_for_status()
        return r.json().get("name")

    async def ping(self) -> bool:
        await self._access_token()
        return True

    async def aclose(self) -> None:
        await self._client.aclose()


_jwks: PyJWKClient | None = None


def verify_google_oidc(token: str, settings: Settings) -> dict[str, Any]:
    """Validate a Google-signed ID token minted for this service by the invoker service account."""
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(_GOOGLE_CERTS, cache_keys=True)
    key = _jwks.get_signing_key_from_jwt(token).key
    claims: dict[str, Any] = jwt.decode(
        token, key, algorithms=["RS256"], audience=settings.service_url.rstrip("/")
    )
    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise jwt.InvalidIssuerError("bad issuer")
    if not claims.get("email_verified") or claims.get("email") != settings.tasks_invoker_sa:
        raise jwt.InvalidTokenError("unexpected caller")
    return claims

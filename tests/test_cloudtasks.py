import base64
import json

import httpx
import pytest

from app.core.cloudtasks import CloudTasksQueue
from app.core.config import Settings


def _settings() -> Settings:
    return Settings(
        queue_backend="cloudtasks",
        gcp_project="proj",
        gcp_location="asia-south1",
        cloud_tasks_queue="pulse-jobs",
        service_url="https://pulse-api-abc.a.run.app/",
        tasks_invoker_sa="invoker@proj.iam.gserviceaccount.com",
    )


def _queue(handler) -> CloudTasksQueue:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if "metadata.google.internal" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return handler(request)

    return CloudTasksQueue(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))


async def test_enqueue_builds_oidc_http_task() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"name": "n"})

    q = _queue(handler)
    await q.enqueue_job("process_statement", "abc", _job_id="statement:abc:1")
    task = seen["body"]["task"]
    assert seen["auth"] == "Bearer tok"
    assert seen["url"].endswith("/projects/proj/locations/asia-south1/queues/pulse-jobs/tasks")
    assert task["name"].endswith("/tasks/statement-abc-1")
    req = task["httpRequest"]
    assert req["url"] == "https://pulse-api-abc.a.run.app/internal/tasks/process_statement"
    assert req["oidcToken"]["audience"] == "https://pulse-api-abc.a.run.app"
    assert json.loads(base64.b64decode(req["body"])) == {"args": ["abc"]}


async def test_duplicate_job_id_is_a_noop() -> None:
    q = _queue(lambda r: httpx.Response(409, json={"error": {"status": "ALREADY_EXISTS"}}))
    assert await q.enqueue_job("process_ingest", "x", _job_id="ingest:x") is None


async def test_server_error_raises() -> None:
    q = _queue(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await q.enqueue_job("process_ingest", "x")

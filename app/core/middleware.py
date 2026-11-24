import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")

log = structlog.get_logger("access")


class RequestIDMiddleware:
    """Pure-ASGI middleware: assigns/propagates a request id, binds it to logs, logs access."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = MutableHeaders(scope=scope).get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and _SAFE_ID.match(incoming) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["path"] not in ("/health", "/ready"):
                log.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_holder["status"],
                    duration_ms=round((time.perf_counter() - start) * 1000, 1),
                )
            structlog.contextvars.clear_contextvars()

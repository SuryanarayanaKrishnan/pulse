"""Push-notification hook point. Nothing is delivered yet: ``LoggingPush`` records what would be sent.

To go live, implement ``PushSender.send`` against FCM (HTTP v1) and return it from ``get_push_sender``;
device tokens would need storing per user (not built).
"""

from typing import Any, Protocol
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


class PushSender(Protocol):
    async def send(
        self, user_id: UUID, title: str, body: str, data: dict[str, Any] | None = None
    ) -> None: ...


class LoggingPush:
    async def send(self, user_id: UUID, title: str, body: str, data: dict[str, Any] | None = None) -> None:
        log.info("push_stub", user_id=str(user_id), title=title, body=body, data=data or {})


class FcmPush:
    """FCM stub: wire the HTTP v1 call and device-token lookup here."""

    async def send(self, user_id: UUID, title: str, body: str, data: dict[str, Any] | None = None) -> None:
        raise NotImplementedError("FCM delivery is not implemented")


def get_push_sender() -> PushSender:
    return LoggingPush()

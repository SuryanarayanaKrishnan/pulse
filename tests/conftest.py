import os
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID, uuid4

# Must be set before the app (and its cached settings) is imported.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-test-secret-test-secret-123456")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# Never let unit tests talk to a real database by accident.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://invalid:invalid@127.0.0.1:1/invalid"

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.db import get_session  # noqa: E402
from app.core.queue import get_queue, get_queue_or_none  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.user import User  # noqa: E402

JWT_SECRET = os.environ["SUPABASE_JWT_SECRET"]


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def make_token() -> Callable[..., str]:
    def _make(
        sub: UUID | None = None,
        *,
        secret: str = JWT_SECRET,
        exp_in: int = 3600,
        aud: str = "authenticated",
        email: str | None = "tester@example.com",
        alg: str = "HS256",
        key: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "sub": str(sub or uuid4()),
            "aud": aud,
            "exp": int(time.time()) + exp_in,
            "email": email,
            "role": "authenticated",
            **(extra or {}),
        }
        return jwt.encode(payload, key if key is not None else secret, algorithm=alg)

    return _make


class FakeSession:
    """Just enough AsyncSession for tests that only exercise auth and the HTTP layer."""

    def __init__(self, user: User | None = None, fail: bool = False) -> None:
        self.user = user
        self.fail = fail

    async def get(self, model: type, id_: Any) -> Any:
        if self.user is not None and model is User:
            return self.user
        return None

    async def execute(self, *a: Any, **k: Any) -> Any:
        if self.fail:
            raise ConnectionError("db down")
        return None

    async def commit(self) -> None: ...


class FakeQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail = fail

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any:
        if self.fail:
            raise ConnectionError("redis down")
        self.jobs.append((function, args, kwargs))
        return object()

    async def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        return True


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def make_client(fake_queue: FakeQueue) -> AsyncIterator[Callable[..., AsyncClient]]:
    clients: list[AsyncClient] = []

    def _make(session: Any = None, queue: Any = None) -> AsyncClient:
        app = create_app()

        async def _session() -> AsyncIterator[Any]:
            yield session or FakeSession()

        async def _queue() -> Any:
            return queue or fake_queue

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[get_queue] = _queue
        app.dependency_overrides[get_queue_or_none] = _queue
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()

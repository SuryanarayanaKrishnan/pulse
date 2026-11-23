"""Integration tests run against a real Postgres (the migration is applied first).

Skipped unless TEST_DATABASE_URL is set, e.g.
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/pulse_test make test-integration
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.db import get_session
from app.core.queue import get_queue, get_queue_or_none
from app.main import create_app
from tests.conftest import FakeQueue

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DB:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    if not TEST_DB:
        return
    env = {**os.environ, "DATABASE_URL": TEST_DB}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)


@pytest.fixture
async def sessionmaker_() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert TEST_DB
    engine = create_async_engine(TEST_DB, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


class Api:
    """Tiny helper: an authenticated client for one fresh user."""

    def __init__(self, client: AsyncClient, user_id: UUID, token: str) -> None:
        self.c, self.user_id, self.h = client, user_id, {"Authorization": f"Bearer {token}"}

    async def get(self, url: str, **kw: Any):
        return await self.c.get(url, headers=self.h, **kw)

    async def post(self, url: str, json: Any = None):
        return await self.c.post(url, json=json, headers=self.h)

    async def patch(self, url: str, json: Any = None):
        return await self.c.patch(url, json=json, headers=self.h)

    async def delete(self, url: str):
        return await self.c.delete(url, headers=self.h)


@pytest.fixture
async def new_user(
    sessionmaker_: async_sessionmaker[AsyncSession], queue: FakeQueue, make_token: Callable[..., str]
) -> AsyncIterator[Callable[[], Api]]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as s:
            yield s

    async def _queue() -> FakeQueue:
        return queue

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_queue] = _queue
    app.dependency_overrides[get_queue_or_none] = _queue
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def _new() -> Api:
        uid = uuid4()
        return Api(client, uid, make_token(uid, email=f"{uid.hex[:8]}@example.com"))

    yield _new
    await client.aclose()

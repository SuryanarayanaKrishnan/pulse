"""Integration tests run against a real Postgres (the migration is applied first).

Skipped unless TEST_DATABASE_URL is set, e.g.
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/pulse_test make test-integration
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.routers.email_connections import get_http_client
from app.api.v1.routers.prices import get_price_providers
from app.core.config import get_settings
from app.core.crypto import Encryptor, generate_key, get_encryptor
from app.core.db import get_session
from app.core.queue import get_queue, get_queue_or_none
from app.main import create_app
from app.services.storage import LocalStorage, get_storage
from app.services.valuations.providers import Providers
from tests.conftest import FakeQueue
from tests.fake_google import FakeGoogle

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
def price_providers() -> Providers:
    """Fake market-data sources shared with the app under test (empty: no network, no sources)."""
    return Providers()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def encryptor() -> Encryptor:
    return Encryptor([generate_key()])


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

    async def raw_get(self, url: str, **kw: Any):
        """Unauthenticated GET (OAuth callback)."""
        return await self.c.get(url, **kw)

    async def put(self, url: str, json: Any = None):
        return await self.c.put(url, json=json, headers=self.h)

    async def delete(self, url: str):
        return await self.c.delete(url, headers=self.h)

    async def upload(self, url: str, data: bytes, filename: str, data_fields: dict[str, str] | None = None):
        return await self.c.post(
            url,
            files={"file": (filename, data, "application/octet-stream")},
            data=data_fields or {},
            headers=self.h,
        )


@pytest.fixture
async def new_user(
    sessionmaker_: async_sessionmaker[AsyncSession],
    queue: FakeQueue,
    make_token: Callable[..., str],
    storage: LocalStorage,
    encryptor: Encryptor,
    google: FakeGoogle,
    price_providers: Providers,
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
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_encryptor] = lambda: encryptor
    app.dependency_overrides[get_price_providers] = lambda: price_providers
    gmail_settings = get_settings().model_copy(
        update={
            "google_client_id": "cid.apps.googleusercontent.com",
            "google_client_secret": "csecret",
            "google_redirect_uri": "https://api.example.com/v1/email-connections/gmail/callback",
            "oauth_state_secret": "t" * 40,
        }
    )
    app.dependency_overrides[get_settings] = lambda: gmail_settings

    async def _http() -> AsyncIterator[AsyncClient]:
        yield google.client()

    app.dependency_overrides[get_http_client] = _http
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def _new() -> Api:
        uid = uuid4()
        return Api(client, uid, make_token(uid, email=f"{uid.hex[:8]}@example.com"))

    yield _new
    await client.aclose()

"""The relay end to end against a real database: arm, claim, grace period, cancel, warnings."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.api.v1.routers import vault_relay
from app.models.vault_relay import VaultRelay
from app.services.vault_relay import GRACE_HOURS, warn_owners

SEALED = '{"app":"pulse-vault","v":1,"ct":"AAAA"}'


@pytest.fixture(autouse=True)
def _fresh_throttle():
    vault_relay._misses.clear()
    yield
    vault_relay._misses.clear()


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send(self, user_id, title, body, data=None) -> None:
        self.sent.append((user_id, title, data))


def new_hash() -> str:
    return uuid4().hex + uuid4().hex


async def arm(api, nominee="n1", code_hash=None, interval=90):
    api.hash = getattr(api, "hash", None) or new_hash()
    return await api.put(
        f"/v1/vault/relay/{nominee}",
        {"sealed_file": SEALED, "claim_code_hash": code_hash or api.hash, "interval_days": interval},
    )


async def age(sessionmaker_, api, days: float, **extra) -> None:
    """Pretend the owner last opened the app ``days`` ago."""
    async with sessionmaker_() as s:
        await s.execute(
            update(VaultRelay)
            .where(VaultRelay.user_id == api.user_id)
            .values(last_heartbeat_at=datetime.now(UTC) - timedelta(days=days), **extra)
        )
        await s.commit()


async def claim(api, code_hash=None):
    return await api.c.post("/v1/vault/relay/claim", json={"claim_code_hash": code_hash or api.hash})


async def test_too_early_then_grace_then_release(new_user, sessionmaker_) -> None:
    api = new_user()
    r = await arm(api)
    assert r.status_code == 200 and r.json()["phase"] == "ok"

    early = (await claim(api)).json()
    assert early["status"] == "not_yet" and early["sealed_file"] is None

    await age(sessionmaker_, api, 91)
    first = (await claim(api)).json()
    assert first["status"] == "pending" and first["sealed_file"] is None

    # Still inside the grace period: not released.
    assert (await claim(api)).json()["status"] == "pending"

    async with sessionmaker_() as s:
        await s.execute(
            update(VaultRelay)
            .where(VaultRelay.user_id == api.user_id)
            .values(claim_requested_at=datetime.now(UTC) - timedelta(hours=GRACE_HOURS + 1))
        )
        await s.commit()
    done = (await claim(api)).json()
    assert done["status"] == "released" and done["sealed_file"] == SEALED
    assert (await api.get("/v1/vault/relay")).json()[0]["phase"] == "released"


async def test_owner_opening_the_app_cancels_a_pending_claim(new_user, sessionmaker_) -> None:
    api = new_user()
    await arm(api)
    await age(sessionmaker_, api, 100)
    assert (await claim(api)).json()["status"] == "pending"
    assert (await api.get("/v1/vault/relay")).json()[0]["phase"] == "claim_pending"

    assert (await api.post("/v1/vault/relay/heartbeat")).status_code == 204
    assert (await api.get("/v1/vault/relay")).json()[0]["phase"] == "ok"
    assert (await claim(api)).json()["status"] == "not_yet"


async def test_explicit_cancel_and_disarm(new_user, sessionmaker_) -> None:
    api = new_user()
    await arm(api)
    await age(sessionmaker_, api, 100)
    await claim(api)
    assert (await api.post("/v1/vault/relay/cancel")).status_code == 204
    assert (await claim(api)).json()["status"] == "not_yet"
    assert (await api.delete("/v1/vault/relay/n1")).status_code == 204
    assert (await api.delete("/v1/vault/relay/n1")).status_code == 404
    assert (await claim(api)).json()["status"] == "wrong_code"


async def test_wrong_codes_are_refused_and_throttled(new_user) -> None:
    api = new_user()
    await arm(api)
    for _ in range(8):
        assert (await claim(api, new_hash())).json()["status"] == "wrong_code"
    assert (await claim(api, new_hash())).status_code == 429


async def test_inputs_are_validated_and_owner_only(new_user) -> None:
    a, b = new_user(), new_user()
    assert (await arm(a, interval=45)).status_code == 422
    assert (await arm(a, code_hash="short")).status_code == 422
    await arm(a)
    assert (await b.get("/v1/vault/relay")).json() == []
    assert (await b.delete("/v1/vault/relay/n1")).status_code == 404
    assert (await a.put("/v1/vault/relay", {"interval_days": 180})).status_code == 204
    assert (await a.get("/v1/vault/relay")).json()[0]["interval_days"] == 180
    assert (await a.c.get("/v1/vault/relay")).status_code == 401


async def test_server_stores_only_ciphertext_and_a_hash(new_user, sessionmaker_) -> None:
    api = new_user()
    await arm(api)
    async with sessionmaker_() as s:
        row = (await s.execute(select(VaultRelay).where(VaultRelay.user_id == api.user_id))).scalars().one()
    assert row.sealed_file == SEALED and row.claim_hash == api.hash
    names = {c.name for c in VaultRelay.__table__.columns}
    assert {"sealed_file", "claim_hash"} <= names
    assert not any("share" in n or n == "claim_code" for n in names)


async def test_warnings_before_and_at_claim_are_sent_once(new_user, sessionmaker_) -> None:
    api = new_user()
    await arm(api)
    push = Recorder()
    async with sessionmaker_() as s:
        assert await warn_owners(s, push, user_id=api.user_id) == 0
        await s.commit()

    await age(sessionmaker_, api, 80)
    async with sessionmaker_() as s:
        assert await warn_owners(s, push, user_id=api.user_id) == 1
        assert await warn_owners(s, push, user_id=api.user_id) == 0  # not repeated
        await s.commit()
    assert push.sent[-1][2]["kind"] == "vault_soon"

    await age(sessionmaker_, api, 95)
    await claim(api)
    async with sessionmaker_() as s:
        assert await warn_owners(s, push, user_id=api.user_id) == 1
        await s.commit()
    assert push.sent[-1][2]["kind"] == "vault_claim"

    # Owner returns: countdown resets and a later silence warns again.
    await api.post("/v1/vault/relay/heartbeat")
    await age(sessionmaker_, api, 80)
    async with sessionmaker_() as s:
        assert await warn_owners(s, push, user_id=api.user_id) == 1
        await s.commit()

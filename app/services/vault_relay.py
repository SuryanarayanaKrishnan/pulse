"""Automatic release of a family-vault emergency file after the owner goes quiet.

The server is deliberately blind: it stores ciphertext and a hash of the claim code, nothing else. The
timing rules are small pure functions so they can be tested without a database.

Flow: the owner's app arms a relay per nominee and sends a heartbeat whenever it is opened. Once no
heartbeat has arrived for ``interval_days`` the nominee may claim with the code. The claim does not
release at once: it starts a grace period (``GRACE_HOURS``) during which the owner is warned and can
cancel just by opening the app. Only a claim that survives the grace period is released.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vault_relay import VaultRelay
from app.services.push import PushSender

INTERVAL_CHOICES = (60, 90, 180, 365)
REMINDER_WINDOW_DAYS = 14
GRACE_HOURS = 72
MAX_FILE_CHARS = 24_000_000


class Phase(StrEnum):
    OK = "ok"
    SOON = "soon"
    OVERDUE = "overdue"
    CLAIM_PENDING = "claim_pending"
    RELEASED = "released"


def release_due_at(last_heartbeat: datetime, interval_days: int) -> datetime:
    return last_heartbeat + timedelta(days=interval_days)


def claim_release_at(claim_requested_at: datetime) -> datetime:
    return claim_requested_at + timedelta(hours=GRACE_HOURS)


def phase_of(relay: VaultRelay, now: datetime) -> Phase:
    if relay.released_at is not None:
        return Phase.RELEASED
    due = release_due_at(relay.last_heartbeat_at, relay.interval_days)
    if now < due:
        return Phase.SOON if due - now <= timedelta(days=REMINDER_WINDOW_DAYS) else Phase.OK
    return Phase.CLAIM_PENDING if relay.claim_requested_at is not None else Phase.OVERDUE


def days_left(relay: VaultRelay, now: datetime) -> int:
    left = release_due_at(relay.last_heartbeat_at, relay.interval_days) - now
    return max(0, -int(-left.total_seconds() // 86400))


@dataclass
class ClaimOutcome:
    status: Literal["released", "pending", "not_yet", "wrong_code"]
    sealed_file: str | None = None
    release_at: datetime | None = None
    days_left: int | None = None


async def arm(
    session: AsyncSession,
    user_id: UUID,
    nominee_id: str,
    sealed_file: str,
    claim_hash: str,
    interval_days: int,
    now: datetime,
) -> VaultRelay:
    """Create or replace the relay for one nominee; a fresh file always restarts the countdown."""
    existing = (
        await session.execute(
            select(VaultRelay).where(VaultRelay.user_id == user_id, VaultRelay.nominee_id == nominee_id)
        )
    ).scalar_one_or_none()
    relay = existing or VaultRelay(user_id=user_id, nominee_id=nominee_id)
    relay.sealed_file = sealed_file
    relay.claim_hash = claim_hash
    relay.interval_days = interval_days
    relay.last_heartbeat_at = now
    relay.claim_requested_at = None
    relay.warned_stage = 0
    relay.released_at = None
    session.add(relay)
    await session.flush()
    return relay


async def disarm(session: AsyncSession, user_id: UUID, nominee_id: str) -> bool:
    relay = (
        await session.execute(
            select(VaultRelay).where(VaultRelay.user_id == user_id, VaultRelay.nominee_id == nominee_id)
        )
    ).scalar_one_or_none()
    if relay is None:
        return False
    await session.delete(relay)
    await session.flush()
    return True


async def heartbeat(session: AsyncSession, user_id: UUID, now: datetime) -> int:
    """The owner is here: restart every countdown and cancel any pending claim.

    A file that has already been released stays released; it is re-armed by sealing a new one.
    """
    res = await session.execute(
        update(VaultRelay)
        .where(VaultRelay.user_id == user_id, VaultRelay.released_at.is_(None))
        .values(last_heartbeat_at=now, claim_requested_at=None, warned_stage=0)
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def set_interval(session: AsyncSession, user_id: UUID, interval_days: int) -> None:
    await session.execute(
        update(VaultRelay)
        .where(VaultRelay.user_id == user_id, VaultRelay.released_at.is_(None))
        .values(interval_days=interval_days, warned_stage=0)
    )


async def claim(session: AsyncSession, claim_hash: str, now: datetime) -> ClaimOutcome:
    relay = (
        await session.execute(select(VaultRelay).where(VaultRelay.claim_hash == claim_hash).with_for_update())
    ).scalar_one_or_none()
    if relay is None:
        return ClaimOutcome("wrong_code")
    phase = phase_of(relay, now)
    if phase is Phase.RELEASED:
        return ClaimOutcome("released", sealed_file=relay.sealed_file)
    if phase in (Phase.OK, Phase.SOON):
        return ClaimOutcome("not_yet", days_left=days_left(relay, now))
    if relay.claim_requested_at is None:
        relay.claim_requested_at = now
        relay.warned_stage = max(relay.warned_stage, 0)  # the next warning sweep tells the owner
        await session.flush()
        return ClaimOutcome("pending", release_at=claim_release_at(now))
    release_at = claim_release_at(relay.claim_requested_at)
    if now < release_at:
        return ClaimOutcome("pending", release_at=release_at)
    relay.released_at = now
    await session.flush()
    return ClaimOutcome("released", sealed_file=relay.sealed_file)


async def warn_owners(
    session: AsyncSession, push: PushSender, now: datetime | None = None, user_id: UUID | None = None
) -> int:
    """Tell owners before and when a claim is made. Each step is sent once per countdown."""
    now = now or datetime.now(UTC)
    query = select(VaultRelay).where(VaultRelay.released_at.is_(None))
    if user_id is not None:
        query = query.where(VaultRelay.user_id == user_id)
    rows = (await session.execute(query)).scalars().all()
    sent = 0
    for relay in rows:
        phase = phase_of(relay, now)
        if phase is Phase.CLAIM_PENDING and relay.warned_stage < 2:
            assert relay.claim_requested_at is not None
            hours = max(0, int((claim_release_at(relay.claim_requested_at) - now).total_seconds() // 3600))
            await push.send(
                relay.user_id,
                "Someone asked to open your family vault",
                f"If this is not right, open Pulse within {hours} hours to stop it.",
                {"kind": "vault_claim", "nominee_id": relay.nominee_id},
            )
            relay.warned_stage = 2
            sent += 1
        elif phase is Phase.OVERDUE and relay.warned_stage < 1:
            await push.send(
                relay.user_id,
                "Your family vault will be shared soon",
                "You have not opened Pulse in a while. Open it to keep your vault private.",
                {"kind": "vault_overdue", "nominee_id": relay.nominee_id},
            )
            relay.warned_stage = 1
            sent += 1
        elif phase is Phase.SOON and relay.warned_stage < 1:
            await push.send(
                relay.user_id,
                "Open Pulse to keep your family vault private",
                f"Your vault will be shared with a trusted person in {days_left(relay, now)} days "
                "if you do not.",
                {"kind": "vault_soon", "nominee_id": relay.nominee_id},
            )
            relay.warned_stage = 1
            sent += 1
    await session.flush()
    return sent

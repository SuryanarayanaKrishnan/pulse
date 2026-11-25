"""Family-vault relay. The server only ever sees ciphertext and a hash of the claim code."""

import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models.vault_relay import VaultRelay
from app.schemas.vault_relay import RelayArm, RelayClaimIn, RelayClaimOut, RelayInterval, RelayOut
from app.services import vault_relay as svc

router = APIRouter(prefix="/vault/relay", tags=["vault"])

# Wrong-code attempts per client address: the claim code is long, this only stops guessing at scale.
_MAX_MISSES, _WINDOW_S = 8, 3600
_misses: dict[str, deque[float]] = defaultdict(deque)


def _blocked(client: str) -> bool:
    q = _misses[client]
    cutoff = time.monotonic() - _WINDOW_S
    while q and q[0] < cutoff:
        q.popleft()
    return len(q) >= _MAX_MISSES


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


def _out(relay: VaultRelay, now: datetime) -> RelayOut:
    return RelayOut(
        nominee_id=relay.nominee_id,
        phase=svc.phase_of(relay, now).value,
        interval_days=relay.interval_days,
        release_due_at=svc.release_due_at(relay.last_heartbeat_at, relay.interval_days),
        claim_release_at=svc.claim_release_at(relay.claim_requested_at) if relay.claim_requested_at else None,
    )


@router.put("/{nominee_id}", response_model=RelayOut)
async def arm(nominee_id: str, payload: RelayArm, user: CurrentUser, session: SessionDep) -> RelayOut:
    if not 1 <= len(nominee_id) <= 64:
        raise AppError("nominee_id must be 1 to 64 characters")
    now = datetime.now(UTC)
    try:
        relay = await svc.arm(
            session,
            user.id,
            nominee_id,
            payload.sealed_file,
            payload.claim_code_hash,
            payload.interval_days,
            now,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("That claim code is already in use. Seal the file again.") from exc
    return _out(relay, now)


@router.delete("/{nominee_id}", status_code=204)
async def disarm(nominee_id: str, user: CurrentUser, session: SessionDep) -> Response:
    if not await svc.disarm(session, user.id, nominee_id):
        raise NotFoundError("No relay for that person")
    await session.commit()
    return Response(status_code=204)


@router.post("/heartbeat", status_code=204)
async def heartbeat(user: CurrentUser, session: SessionDep) -> Response:
    """The owner opened the app. Also cancels any pending claim."""
    await svc.heartbeat(session, user.id, datetime.now(UTC))
    await session.commit()
    return Response(status_code=204)


@router.post("/cancel", status_code=204)
async def cancel(user: CurrentUser, session: SessionDep) -> Response:
    """Stop a pending claim. Same effect as a heartbeat; a separate verb so the app can say so."""
    await svc.heartbeat(session, user.id, datetime.now(UTC))
    await session.commit()
    return Response(status_code=204)


@router.put("", status_code=204)
async def set_interval(payload: RelayInterval, user: CurrentUser, session: SessionDep) -> Response:
    await svc.set_interval(session, user.id, payload.interval_days)
    await session.commit()
    return Response(status_code=204)


@router.get("", response_model=list[RelayOut])
async def status(user: CurrentUser, session: SessionDep) -> list[RelayOut]:
    now = datetime.now(UTC)
    rows = await session.execute(
        select(VaultRelay).where(VaultRelay.user_id == user.id).order_by(VaultRelay.created_at)
    )
    return [_out(r, now) for r in rows.scalars()]


@router.post("/claim", response_model=RelayClaimOut)
async def claim(payload: RelayClaimIn, request: Request, session: SessionDep) -> RelayClaimOut:
    """Called by the nominee, who has no account. Authenticated only by the claim code."""
    client = request.client.host if request.client else "unknown"
    if _blocked(client):
        raise RateLimited("Too many wrong codes. Try again later.")
    out = await svc.claim(session, payload.claim_code_hash, datetime.now(UTC))
    if out.status == "wrong_code":
        _misses[client].append(time.monotonic())
    await session.commit()
    return RelayClaimOut(**out.__dict__)

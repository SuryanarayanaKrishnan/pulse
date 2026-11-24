from typing import Annotated

import structlog
from fastapi import APIRouter, Body, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, IngestUser, QueueDep, SessionDep
from app.core.errors import NotFoundError
from app.models.integration import IngestToken
from app.models.statement import ParseStatus
from app.models.transaction import RawIngest, Transaction, TxnSource
from app.schemas.ingest import IngestOut, IngestRequest, WalletIngestOut, WalletTap
from app.services.ingest import process_raw_ingest, store_raw
from app.services.ingest_tokens import rotate_token
from app.services.push import get_push_sender
from app.services.rewards_service import nudge_for_transaction

router = APIRouter(prefix="/ingest", tags=["ingest"])
log = structlog.get_logger(__name__)


async def _ingest(
    channel: TxnSource,
    req: IngestRequest,
    user: IngestUser,
    session: SessionDep,
    queue: QueueDep,
    response: Response,
) -> IngestOut:
    row, duplicate = await store_raw(session, user.id, channel, req)
    await session.commit()
    if duplicate:
        response.status_code = 200
    else:
        response.status_code = 202
        try:
            await queue.enqueue_job("process_ingest", str(row.id), _job_id=f"ingest:{row.id}")
        except Exception:
            # The raw payload is safely stored; a sweeper can re-enqueue pending rows later.
            log.exception("ingest_enqueue_failed", ingest_id=str(row.id))
    return IngestOut(id=row.id, duplicate=duplicate, parse_status=row.parse_status)


async def _wallet(
    channel: TxnSource,
    body: IngestRequest | WalletTap,
    user: IngestUser,
    session: SessionDep,
    queue: QueueDep,
    response: Response,
) -> WalletIngestOut:
    """Wallet taps are structured and cheap, so they are processed inline: the response already carries
    the transaction and any rewards nudge, ready to show right after the tap."""
    req = body.to_request() if isinstance(body, WalletTap) else body
    row, duplicate = await store_raw(session, user.id, channel, req)
    await session.commit()
    response.status_code = 200 if duplicate else 202
    if duplicate:
        return WalletIngestOut(
            id=row.id, duplicate=True, parse_status=row.parse_status, transaction_id=row.transaction_id
        )
    try:
        status = await process_raw_ingest(session, row.id)
    except Exception:
        await session.rollback()
        log.exception("wallet_inline_failed", ingest_id=str(row.id))
        await queue.enqueue_job("process_ingest", str(row.id), _job_id=f"ingest:{row.id}")
        return WalletIngestOut(id=row.id, duplicate=False, parse_status=ParseStatus.pending)
    fresh = await session.get(RawIngest, row.id, populate_existing=True)
    assert fresh is not None
    row = fresh
    txn = await session.get(Transaction, row.transaction_id) if row.transaction_id else None
    nudge = await nudge_for_transaction(session, user.id, txn) if txn is not None else None
    if nudge is not None:
        await get_push_sender().send(user.id, "Card tip", nudge["message"], {"type": "rewards_nudge"})
    return WalletIngestOut(
        id=row.id,
        duplicate=False,
        parse_status=status,
        transaction_id=row.transaction_id,
        merged_with_existing=bool((row.parse_result or {}).get("merged_with_existing_transaction")),
        nudge=nudge,
    )


@router.post("/sms", response_model=IngestOut, status_code=202)
async def ingest_sms(
    req: IngestRequest, user: IngestUser, session: SessionDep, queue: QueueDep, response: Response
) -> IngestOut:
    return await _ingest(TxnSource.sms, req, user, session, queue, response)


@router.post("/notification", response_model=IngestOut, status_code=202)
async def ingest_notification(
    req: IngestRequest, user: IngestUser, session: SessionDep, queue: QueueDep, response: Response
) -> IngestOut:
    return await _ingest(TxnSource.notification, req, user, session, queue, response)


WalletBody = Annotated[IngestRequest | WalletTap, Body()]


@router.post("/apple-shortcut", response_model=WalletIngestOut, status_code=202)
async def ingest_apple_shortcut(
    body: WalletBody, user: IngestUser, session: SessionDep, queue: QueueDep, response: Response
) -> WalletIngestOut:
    """iOS Shortcuts "Transaction" automation. Send `{amount, merchant, card, last4?, timestamp, currency}`
    with `Authorization: Bearer pls_...` (or `?token=`), or the older IngestRequest shape."""
    return await _wallet(TxnSource.apple_shortcut, body, user, session, queue, response)


@router.post("/google-pay", response_model=WalletIngestOut, status_code=202)
async def ingest_google_pay(
    body: WalletBody, user: IngestUser, session: SessionDep, queue: QueueDep, response: Response
) -> WalletIngestOut:
    return await _wallet(TxnSource.google_wallet, body, user, session, queue, response)


@router.post("/samsung-wallet", response_model=WalletIngestOut, status_code=202)
async def ingest_samsung_wallet(
    body: WalletBody, user: IngestUser, session: SessionDep, queue: QueueDep, response: Response
) -> WalletIngestOut:
    return await _wallet(TxnSource.samsung_wallet, body, user, session, queue, response)


class TokenStatus(BaseModel):
    exists: bool
    prefix: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None


class TokenCreated(BaseModel):
    token: str
    prefix: str
    note: str = "Shown once. Store it in the Shortcut; rotating invalidates the previous token."


@router.post("/token", response_model=TokenCreated, status_code=201, tags=["ingest-token"])
async def create_or_rotate_token(user: CurrentUser, session: SessionDep) -> TokenCreated:
    """Generate (or rotate) the per-user ingest token. Requires the normal Supabase session."""
    token, row = await rotate_token(session, user.id)
    await session.commit()
    return TokenCreated(token=token, prefix=row.prefix)


@router.get("/token", response_model=TokenStatus, tags=["ingest-token"])
async def token_status(user: CurrentUser, session: SessionDep) -> TokenStatus:
    row = (
        await session.execute(select(IngestToken).where(IngestToken.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        return TokenStatus(exists=False)
    return TokenStatus(
        exists=True,
        prefix=row.prefix,
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
    )


@router.delete("/token", status_code=204, tags=["ingest-token"])
async def revoke_token(user: CurrentUser, session: SessionDep) -> Response:
    row = (
        await session.execute(select(IngestToken).where(IngestToken.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("No ingest token")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)

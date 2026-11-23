"""Ingestion: store raw payloads (deduped) and turn them into transactions."""

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.statement import ParseStatus
from app.models.transaction import Direction, RawIngest, Transaction, TxnSource
from app.schemas.ingest import IngestRequest
from app.services.categorisation import CategoryResolver
from app.services.institutions import institutions
from app.services.parsers import ParsedTransaction, ParserRegistry
from app.services.parsers import registry as default_registry

log = structlog.get_logger(__name__)
_WS = re.compile(r"\s+")


def compute_external_hash(user_id: UUID, channel: TxnSource, req: IngestRequest) -> str:
    """Stable dedupe key. A client-provided external_id wins; otherwise hash the content.

    The timestamp is included when supplied so two genuinely identical messages at different
    times are not collapsed; clients that cannot supply one should send external_id.
    """
    if req.external_id:
        basis = f"id:{req.external_id}"
    else:
        text = _WS.sub(" ", (req.raw_text or "").strip()).lower()
        fields = req.fields.model_dump_json() if req.fields else ""
        ts = req.received_at.astimezone(UTC).isoformat() if req.received_at else ""
        basis = f"{req.sender or ''}|{text}|{fields}|{ts}"
    return hashlib.sha256(f"{user_id}|{channel.value}|{basis}".encode()).hexdigest()


async def store_raw(
    session: AsyncSession, user_id: UUID, channel: TxnSource, req: IngestRequest
) -> tuple[RawIngest, bool]:
    """Insert the raw payload; returns (row, is_duplicate). Race-safe via ON CONFLICT."""
    ext_hash = compute_external_hash(user_id, channel, req)
    stmt = (
        pg_insert(RawIngest)
        .values(
            user_id=user_id,
            channel=channel,
            external_hash=ext_hash,
            payload=req.model_dump(mode="json", exclude_none=True),
        )
        .on_conflict_do_nothing(constraint="uq_raw_ingests_user_id_external_hash")
        .returning(RawIngest.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        row = await session.get(RawIngest, new_id)
        assert row is not None
        return row, False
    row = (
        await session.execute(
            select(RawIngest).where(RawIngest.user_id == user_id, RawIngest.external_hash == ext_hash)
        )
    ).scalar_one()
    return row, True


def _from_structured(payload: dict[str, Any]) -> ParsedTransaction | None:
    f = payload.get("fields")
    if not f:
        return None
    posted = datetime.fromisoformat(f["posted_at"]) if f.get("posted_at") else None
    return ParsedTransaction(
        amount_minor=int(f["amount_minor"]),
        currency=f.get("currency", "AED"),
        direction=Direction(f.get("direction", "debit")),
        merchant_raw=f.get("merchant"),
        last4=f.get("last4"),
        posted_at=posted,
        confidence=1.0,
    )


async def _resolve_account(
    session: AsyncSession, user_id: UUID, payload: dict[str, Any], parsed: ParsedTransaction
) -> UUID | None:
    if payload.get("account_id"):
        acc = await session.execute(
            select(Account.id).where(
                Account.id == UUID(payload["account_id"]),
                Account.user_id == user_id,
                Account.deleted_at.is_(None),
            )
        )
        return acc.scalar_one_or_none()
    if parsed.last4:
        accounts = list(
            (
                await session.execute(
                    select(Account).where(
                        Account.user_id == user_id,
                        Account.last4 == parsed.last4,
                        Account.deleted_at.is_(None),
                    )
                )
            ).scalars()
        )
        if hint := payload.get("institution"):
            # An explicit institution from the client is a hard filter.
            key = institutions.resolve_key(hint)
            accounts = [
                a
                for a in accounts
                if (a.institution or "").lower() == hint.lower()
                or (key is not None and institutions.resolve_key(a.institution) == key)
            ]
        elif parsed.institution and len(accounts) > 1:
            # Institution recognised from the SMS itself is only a tie-breaker.
            matching = [a for a in accounts if institutions.resolve_key(a.institution) == parsed.institution]
            accounts = matching or accounts
        if len(accounts) == 1:
            return accounts[0].id
    return None


async def process_raw_ingest(
    session: AsyncSession, ingest_id: UUID, registry: ParserRegistry = default_registry
) -> ParseStatus:
    """Parse a stored payload and create a transaction when the account can be resolved.

    Outcomes: parsed (transaction created), needs_review (parsed but account unknown),
    skipped (not a transaction, e.g. an OTP), failed (unexpected error).
    """
    row = await session.get(RawIngest, ingest_id)
    if row is None:
        log.warning("ingest_missing", ingest_id=str(ingest_id))
        return ParseStatus.failed
    if row.parse_status in (ParseStatus.parsed, ParseStatus.skipped):
        return row.parse_status

    payload = row.payload
    received_at = datetime.fromisoformat(payload["received_at"]) if payload.get("received_at") else None
    parsed = _from_structured(payload)
    if parsed is None:
        text = " ".join(filter(None, [payload.get("title"), payload.get("raw_text")]))
        parsed = registry.parse(
            text,
            institution=payload.get("institution"),
            sender=payload.get("sender"),
            received_at=received_at,
        )
    if parsed is None:
        row.parse_status = ParseStatus.skipped
        row.parse_result = {"reason": "not_a_transaction"}
        await session.commit()
        return row.parse_status

    row.parse_result = {
        "amount_minor": parsed.amount_minor,
        "currency": parsed.currency,
        "direction": parsed.direction.value,
        "merchant_raw": parsed.merchant_raw,
        "last4": parsed.last4,
        "confidence": parsed.confidence,
    }
    account_id = await _resolve_account(session, row.user_id, payload, parsed)
    if account_id is None:
        row.parse_status = ParseStatus.needs_review
        await session.commit()
        return row.parse_status

    enriched = (await CategoryResolver.load(session, row.user_id)).enrich(
        parsed.merchant_raw, parsed.direction
    )
    txn = Transaction(
        user_id=row.user_id,
        account_id=account_id,
        amount_minor=parsed.amount_minor,
        currency=parsed.currency,
        direction=parsed.direction,
        merchant_raw=parsed.merchant_raw,
        merchant_normalized=enriched.merchant_normalized,
        category_id=enriched.category_id,
        posted_at=parsed.posted_at or received_at or row.created_at,
        source=row.channel,
        external_hash=row.external_hash,
    )
    session.add(txn)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        row = await session.get(RawIngest, ingest_id)
        assert row is not None
        row.parse_status = ParseStatus.skipped
        row.parse_result = {"reason": "duplicate_transaction"}
        await session.commit()
        return row.parse_status
    row.transaction_id = txn.id
    row.parse_status = ParseStatus.parsed
    await session.commit()
    return row.parse_status

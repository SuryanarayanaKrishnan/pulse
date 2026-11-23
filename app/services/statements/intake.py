"""Create a ``statements`` row from file bytes (upload or Gmail), idempotently."""

import hashlib
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.statement import ParseStatus, Statement
from app.services.storage import Storage

log = structlog.get_logger(__name__)

_EXT = {"pdf": "pdf", "csv": "csv"}


def storage_key(user_id: UUID, kind: str) -> str:
    return f"statements/{user_id}/{uuid4().hex}.{_EXT.get(kind, 'bin')}"


async def create_statement(
    session: AsyncSession,
    storage: Storage,
    user_id: UUID,
    data: bytes,
    *,
    kind: str,
    filename: str | None,
    content_type: str | None,
    source: str,
    institution: str | None = None,
    account_id: UUID | None = None,
    email_connection_id: UUID | None = None,
    external_id: str | None = None,
) -> tuple[Statement, bool]:
    """Returns (statement, duplicate). Duplicate = same bytes already stored, or same external_id seen.

    The same content never creates two live statements, and a Gmail attachment (``external_id``) is
    never imported twice even when the earlier statement was soft-deleted.
    """
    sha = hashlib.sha256(data).hexdigest()
    existing = await _find_existing(session, user_id, sha, external_id)
    if existing is not None:
        return existing, True

    key = storage_key(user_id, kind)
    await storage.put(key, data, "application/pdf" if kind == "pdf" else "text/csv")
    stmt = Statement(
        user_id=user_id,
        account_id=account_id,
        currency="AED",
        file_storage_key=key,
        file_sha256=sha,
        original_filename=(filename or "")[:255] or None,
        content_type=(content_type or "")[:100] or None,
        institution=institution,
        source=source,
        email_connection_id=email_connection_id,
        external_id=external_id,
        parse_status=ParseStatus.pending,
    )
    try:
        async with session.begin_nested():
            session.add(stmt)
            await session.flush()
    except IntegrityError:
        # Lost a race with a concurrent insert of the same content/external id.
        await storage.delete(key)
        existing = await _find_existing(session, user_id, sha, external_id)
        if existing is None:
            raise
        return existing, True
    return stmt, False


async def _find_existing(
    session: AsyncSession, user_id: UUID, sha: str, external_id: str | None
) -> Statement | None:
    live_by_hash = select(Statement).where(
        Statement.user_id == user_id, Statement.file_sha256 == sha, Statement.deleted_at.is_(None)
    )
    if (found := (await session.execute(live_by_hash)).scalars().first()) is not None:
        return found
    if external_id:
        by_ext = select(Statement).where(Statement.user_id == user_id, Statement.external_id == external_id)
        return (await session.execute(by_ext)).scalars().first()
    return None

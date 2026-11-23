"""Per-user ingest tokens: generated once, stored hashed, usable without a Supabase session."""

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IngestToken
from app.models.user import User

TOKEN_PREFIX = "pls_"  # noqa: S105


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def rotate_token(session: AsyncSession, user_id: UUID) -> tuple[str, IngestToken]:
    """Create or replace the user's token. Returns the plaintext (shown once) and the row."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    values = {"token_hash": hash_token(token), "prefix": token[: len(TOKEN_PREFIX) + 6], "last_used_at": None}
    await session.execute(
        pg_insert(IngestToken)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            constraint="uq_ingest_tokens_user_id", set_={**values, "created_at": datetime.now(UTC)}
        )
    )
    row = (await session.execute(select(IngestToken).where(IngestToken.user_id == user_id))).scalar_one()
    await session.refresh(row)
    return token, row


async def user_for_ingest_token(session: AsyncSession, token: str) -> User | None:
    if not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        return None
    row = (
        await session.execute(select(IngestToken).where(IngestToken.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.deleted_at is not None:
        return None
    await session.execute(
        update(IngestToken).where(IngestToken.id == row.id).values(last_used_at=datetime.now(UTC))
    )
    await session.commit()
    return user

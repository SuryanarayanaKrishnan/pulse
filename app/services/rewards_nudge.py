"""Rewards nudge after a captured transaction (filled in by the rewards engine)."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def nudge_for_transaction(
    session: AsyncSession, user_id: UUID, txn: Transaction
) -> dict[str, Any] | None:
    return None

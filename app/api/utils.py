from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError


async def get_owned[M](session: AsyncSession, model: type[M], id_: UUID, user_id: UUID) -> M:
    """Fetch a row owned by the user (and not soft-deleted) or raise 404.

    404 rather than 403 so ids of other users' rows cannot be probed.
    """
    cols: Any = model
    stmt = select(model).where(cols.id == id_, cols.user_id == user_id)
    if hasattr(model, "deleted_at"):
        stmt = stmt.where(cols.deleted_at.is_(None))
    obj = (await session.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise NotFoundError(f"{model.__name__} not found")
    return obj


def apply_updates(obj: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        setattr(obj, key, value)

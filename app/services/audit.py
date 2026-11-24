from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


def record_audit(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: object | None = None,
    request_id: str | None = None,
    ip: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the current unit of work (committed with the caller's transaction)."""
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            request_id=request_id,
            ip=ip,
            data=data or {},
        )
    )

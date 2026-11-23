from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates
from app.models.user import User
from app.schemas.user import MeOut, MeUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeOut)
async def get_me(user: CurrentUser) -> User:
    return user


@router.patch("", response_model=MeOut)
async def update_me(payload: MeUpdate, user: CurrentUser, session: SessionDep) -> User:
    apply_updates(user, payload.model_dump(exclude_unset=True, exclude_none=True))
    record_audit(session, user_id=user.id, action="user.update", entity_type="user", entity_id=user.id)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("", status_code=204)
async def deactivate_me(user: CurrentUser, session: SessionDep, request: Request) -> Response:
    """Soft-deactivate. Hard erasure (GDPR-style) is a separate offline process."""
    user.deleted_at = datetime.now(UTC)
    record_audit(
        session,
        user_id=user.id,
        action="user.deactivate",
        entity_type="user",
        entity_id=user.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return Response(status_code=204)

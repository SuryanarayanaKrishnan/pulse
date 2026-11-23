from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates
from app.core.errors import ConflictError, DomainValidationError
from app.models.user import User
from app.schemas.user import MeOut, MeUpdate
from app.services.audit import record_audit
from app.services.phone import InvalidPhoneError, is_uae_mobile, normalise_phone, phone_hash

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeOut)
async def get_me(user: CurrentUser) -> User:
    return user


def _phone(raw: str | None, *, code: str, uae_mobile_only: bool = False) -> str | None:
    if raw is None or not raw.strip():
        return None  # clears the field
    try:
        e164 = normalise_phone(raw)
    except InvalidPhoneError as exc:
        raise DomainValidationError(str(exc), code=code) from exc
    if uae_mobile_only and not is_uae_mobile(e164):
        raise DomainValidationError("Aani needs a UAE mobile number (05x...)", code=code)
    return e164


@router.patch("", response_model=MeOut)
async def update_me(payload: MeUpdate, user: CurrentUser, session: SessionDep) -> User:
    """Update the profile. `phone` / `aani_phone` accept UAE shorthand (050 123 4567) and are stored as E.164;
    send null or "" to clear one."""
    fields = payload.model_fields_set
    apply_updates(
        user,
        payload.model_dump(
            include={"display_name", "default_currency", "phone_discoverable"} & fields, exclude_none=True
        ),
    )
    if "phone" in fields:
        user.phone_e164 = _phone(payload.phone, code="invalid_phone")
        user.phone_hash = phone_hash(user.phone_e164) if user.phone_e164 else None
    if "aani_phone" in fields:
        user.aani_phone_e164 = _phone(payload.aani_phone, code="invalid_aani_phone", uae_mobile_only=True)
    record_audit(session, user_id=user.id, action="user.update", entity_type="user", entity_id=user.id)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("This phone number belongs to another account", code="phone_in_use") from exc
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

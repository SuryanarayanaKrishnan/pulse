from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models.user import User
from app.schemas.contacts import ContactMatch, ContactMatchIn, ContactMatchOut

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("/match", response_model=ContactMatchOut)
async def match_contacts(payload: ContactMatchIn, user: CurrentUser, session: SessionDep) -> ContactMatchOut:
    """Which of these phone hashes belong to Pulse users? The client sends only hashes; nothing sent
    here is stored or logged by the API, and only users who have set a phone number and left
    `phone_discoverable` on can be found. You never match yourself."""
    rows = await session.execute(
        select(User.phone_hash, User.id, User.display_name).where(
            User.phone_hash.in_(payload.hashes),
            User.phone_discoverable.is_(True),
            User.deleted_at.is_(None),
            User.id != user.id,
        )
    )
    return ContactMatchOut(
        matches=[ContactMatch(phone_hash=h, user_id=uid, display_name=name) for h, uid, name in rows if h]
    )

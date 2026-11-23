from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import UnauthorizedError
from app.core.queue import QueuePool, get_queue
from app.core.security import TokenClaims, verify_token
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QueueDep = Annotated[QueuePool, Depends(get_queue)]


async def get_claims(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: SettingsDep,
) -> TokenClaims:
    if creds is None:
        raise UnauthorizedError("Missing bearer token", code="missing_token")
    return await verify_token(creds.credentials, settings)


async def get_current_user(claims: Annotated[TokenClaims, Depends(get_claims)], session: SessionDep) -> User:
    """Resolve the Supabase user to our row, provisioning it on first sight."""
    user = await session.get(User, claims.sub)
    if user is None or user.deleted_at is not None:
        if user is None:
            await session.execute(
                pg_insert(User)
                .values(id=claims.sub, email=claims.email)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await session.commit()
            user = (await session.execute(select(User).where(User.id == claims.sub))).scalar_one()
        if user.deleted_at is not None:
            raise UnauthorizedError("Account is deactivated", code="account_deactivated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

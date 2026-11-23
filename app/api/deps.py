from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import Encryptor, get_encryptor
from app.core.db import get_session
from app.core.errors import UnauthorizedError
from app.core.queue import QueuePool, get_queue
from app.core.security import TokenClaims, verify_token
from app.models.user import User
from app.services.ingest_tokens import TOKEN_PREFIX, user_for_ingest_token
from app.services.storage import Storage, get_storage

_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QueueDep = Annotated[QueuePool, Depends(get_queue)]
StorageDep = Annotated[Storage, Depends(get_storage)]
EncryptorDep = Annotated[Encryptor, Depends(get_encryptor)]


async def get_claims(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: SettingsDep,
) -> TokenClaims:
    if creds is None:
        raise UnauthorizedError("Missing bearer token", code="missing_token")
    return await verify_token(creds.credentials, settings)


async def _user_for_claims(claims: TokenClaims, session: AsyncSession) -> User:
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


async def get_current_user(claims: Annotated[TokenClaims, Depends(get_claims)], session: SessionDep) -> User:
    return await _user_for_claims(claims, session)


async def get_ingest_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: SettingsDep,
    session: SessionDep,
    token: Annotated[
        str | None, Query(description="Ingest token (alternative to the Authorization header)")
    ] = None,
) -> User:
    """Capture endpoints accept a Supabase JWT *or* the user's ingest token (``pls_...``), the latter via
    ``Authorization: Bearer`` or ``?token=`` so unattended automations (iOS Shortcuts) can post."""
    raw = creds.credentials if creds is not None and creds.credentials.startswith(TOKEN_PREFIX) else token
    if raw:
        user = await user_for_ingest_token(session, raw)
        if user is None:
            raise UnauthorizedError("Invalid or revoked ingest token", code="invalid_ingest_token")
        return user
    return await _user_for_claims(await get_claims(creds, settings), session)


CurrentUser = Annotated[User, Depends(get_current_user)]
IngestUser = Annotated[User, Depends(get_ingest_user)]

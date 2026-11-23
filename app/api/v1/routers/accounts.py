from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates, get_owned
from app.models.account import Account
from app.schemas.account import AccountCreate, AccountOut, AccountUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(user: CurrentUser, session: SessionDep) -> list[Account]:
    rows = await session.execute(
        select(Account)
        .where(Account.user_id == user.id, Account.deleted_at.is_(None))
        .order_by(Account.created_at)
    )
    return list(rows.scalars())


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(payload: AccountCreate, user: CurrentUser, session: SessionDep) -> Account:
    account = Account(user_id=user.id, **payload.model_dump())
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: UUID, user: CurrentUser, session: SessionDep) -> Account:
    return await get_owned(session, Account, account_id, user.id)


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: UUID, payload: AccountUpdate, user: CurrentUser, session: SessionDep
) -> Account:
    account = await get_owned(session, Account, account_id, user.id)
    apply_updates(account, payload.model_dump(exclude_unset=True))
    await session.commit()
    await session.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
async def delete_account(
    account_id: UUID, user: CurrentUser, session: SessionDep, request: Request
) -> Response:
    account = await get_owned(session, Account, account_id, user.id)
    account.deleted_at = datetime.now(UTC)
    record_audit(
        session,
        user_id=user.id,
        action="account.delete",
        entity_type="account",
        entity_id=account.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return Response(status_code=204)

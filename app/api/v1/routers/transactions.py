import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates, get_owned
from app.core.errors import ConflictError, DomainValidationError
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Direction, Transaction, TxnSource
from app.schemas.common import Page
from app.schemas.transaction import TransactionCreate, TransactionOut, TransactionUpdate
from app.services.merchants import normalize_merchant
from app.services.pagination import decode_cursor, encode_cursor

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=Page[TransactionOut])
async def list_transactions(
    user: CurrentUser,
    session: SessionDep,
    account_id: UUID | None = None,
    category_id: UUID | None = None,
    direction: Direction | None = None,
    source: TxnSource | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_amount_minor: Annotated[int | None, Query(ge=0)] = None,
    max_amount_minor: Annotated[int | None, Query(ge=0)] = None,
    q: Annotated[str | None, Query(max_length=100, description="Merchant substring")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[TransactionOut]:
    stmt = select(Transaction).where(Transaction.user_id == user.id, Transaction.deleted_at.is_(None))
    if account_id:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category_id:
        stmt = stmt.where(Transaction.category_id == category_id)
    if direction:
        stmt = stmt.where(Transaction.direction == direction)
    if source:
        stmt = stmt.where(Transaction.source == source)
    if date_from:
        stmt = stmt.where(Transaction.posted_at >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.posted_at < date_to)
    if min_amount_minor is not None:
        stmt = stmt.where(Transaction.amount_minor >= min_amount_minor)
    if max_amount_minor is not None:
        stmt = stmt.where(Transaction.amount_minor <= max_amount_minor)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            Transaction.merchant_normalized.ilike(pattern, escape="\\")
            | Transaction.merchant_raw.ilike(pattern, escape="\\")
        )
    if cursor:
        c_posted, c_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(Transaction.posted_at, Transaction.id) < tuple_(c_posted, c_id))

    stmt = stmt.order_by(Transaction.posted_at.desc(), Transaction.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].posted_at, rows[-1].id)
    return Page[TransactionOut](
        items=[TransactionOut.model_validate(r) for r in rows], next_cursor=next_cursor
    )


@router.post("", response_model=TransactionOut, status_code=201)
async def create_transaction(
    payload: TransactionCreate, user: CurrentUser, session: SessionDep
) -> Transaction:
    account = await get_owned(session, Account, payload.account_id, user.id)
    if payload.category_id:
        category = await session.get(Category, payload.category_id)
        if category is None or category.user_id not in (None, user.id):
            raise DomainValidationError("Unknown category", code="invalid_category")
    data = payload.model_dump()
    data["currency"] = payload.currency or account.currency
    data["merchant_normalized"] = payload.merchant_normalized or normalize_merchant(payload.merchant_raw)
    txn = Transaction(user_id=user.id, source=TxnSource.manual, **data)
    if txn.external_hash:
        # Clients may supply their own dedupe key; scope it so it can't collide with ingest hashes.
        txn.external_hash = hashlib.sha256(f"manual:{txn.external_hash}".encode()).hexdigest()
    session.add(txn)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Duplicate transaction", code="duplicate_transaction") from exc
    await session.refresh(txn)
    return txn


@router.get("/{transaction_id}", response_model=TransactionOut)
async def get_transaction(transaction_id: UUID, user: CurrentUser, session: SessionDep) -> Transaction:
    return await get_owned(session, Transaction, transaction_id, user.id)


@router.patch("/{transaction_id}", response_model=TransactionOut)
async def update_transaction(
    transaction_id: UUID, payload: TransactionUpdate, user: CurrentUser, session: SessionDep
) -> Transaction:
    txn = await get_owned(session, Transaction, transaction_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("category_id"):
        category = await session.get(Category, data["category_id"])
        if category is None or category.user_id not in (None, user.id):
            raise DomainValidationError("Unknown category", code="invalid_category")
    apply_updates(txn, data)
    await session.commit()
    await session.refresh(txn)
    return txn


@router.delete("/{transaction_id}", status_code=204)
async def delete_transaction(transaction_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    txn = await get_owned(session, Transaction, transaction_id, user.id)
    txn.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)

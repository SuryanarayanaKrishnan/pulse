from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import DomainValidationError, NotFoundError
from app.models.split import (
    Settlement,
    SplitExpense,
    SplitGroup,
    SplitMember,
    SplitShare,
    SplitType,
)
from app.models.user import User
from app.schemas.split import (
    BalanceOut,
    ExpenseCreate,
    ExpenseOut,
    GroupBalancesOut,
    GroupCreate,
    GroupOut,
    MemberIn,
    MemberOut,
    SettlementCreate,
    SettlementOut,
    ShareOut,
    TransferOut,
)
from app.services import splits as split_math

router = APIRouter(prefix="/splits", tags=["splits"])


async def _load_group(session: AsyncSession, group_id: UUID, user: User) -> SplitGroup:
    """A group is visible to its members (by linked user id) only."""
    stmt = select(SplitGroup).where(
        SplitGroup.id == group_id,
        SplitGroup.deleted_at.is_(None),
        exists().where(SplitMember.group_id == SplitGroup.id, SplitMember.user_id == user.id),
    )
    group = (await session.execute(stmt)).scalar_one_or_none()
    if group is None:
        raise NotFoundError("Group not found")
    return group


async def _members(session: AsyncSession, group_id: UUID) -> list[SplitMember]:
    rows = await session.execute(
        select(SplitMember)
        .where(SplitMember.group_id == group_id)
        .order_by(SplitMember.created_at, SplitMember.id)
    )
    return list(rows.scalars())


async def _add_member(session: AsyncSession, group_id: UUID, m: MemberIn) -> SplitMember:
    if m.user_id is not None:
        if await session.get(User, m.user_id) is None:
            raise DomainValidationError("Unknown user_id", code="invalid_member")
        dup = await session.execute(
            select(SplitMember.id).where(SplitMember.group_id == group_id, SplitMember.user_id == m.user_id)
        )
        if dup.scalar_one_or_none() is not None:
            raise DomainValidationError("User is already a member", code="duplicate_member")
    member = SplitMember(group_id=group_id, **m.model_dump())
    session.add(member)
    return member


def _group_out(group: SplitGroup, members: list[SplitMember]) -> GroupOut:
    out = GroupOut.model_validate(group)
    out.members = [MemberOut.model_validate(m) for m in members]
    return out


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(payload: GroupCreate, user: CurrentUser, session: SessionDep) -> GroupOut:
    group = SplitGroup(created_by=user.id, name=payload.name, currency=payload.currency)
    session.add(group)
    await session.flush()
    await _add_member(
        session, group.id, MemberIn(display_name=user.display_name or user.email or "Me", user_id=user.id)
    )
    for m in payload.members:
        if m.user_id == user.id:
            continue
        await _add_member(session, group.id, m)
    await session.commit()
    await session.refresh(group)
    return _group_out(group, await _members(session, group.id))


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(user: CurrentUser, session: SessionDep) -> list[GroupOut]:
    groups = (
        (
            await session.execute(
                select(SplitGroup)
                .where(
                    SplitGroup.deleted_at.is_(None),
                    exists().where(SplitMember.group_id == SplitGroup.id, SplitMember.user_id == user.id),
                )
                .order_by(SplitGroup.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_group_out(g, await _members(session, g.id)) for g in groups]


@router.get("/groups/{group_id}", response_model=GroupOut)
async def get_group(group_id: UUID, user: CurrentUser, session: SessionDep) -> GroupOut:
    group = await _load_group(session, group_id, user)
    return _group_out(group, await _members(session, group.id))


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    group = await _load_group(session, group_id, user)
    if group.created_by != user.id:
        raise DomainValidationError("Only the group creator can delete it", code="not_group_owner")
    group.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.post("/groups/{group_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    group_id: UUID, payload: MemberIn, user: CurrentUser, session: SessionDep
) -> SplitMember:
    group = await _load_group(session, group_id, user)
    member = await _add_member(session, group.id, payload)
    await session.commit()
    await session.refresh(member)
    return member


def _expense_out(e: SplitExpense, shares: list[SplitShare]) -> ExpenseOut:
    out = ExpenseOut.model_validate(e)
    out.shares = [ShareOut.model_validate(s) for s in shares]
    return out


@router.post("/groups/{group_id}/expenses", response_model=ExpenseOut, status_code=201)
async def create_expense(
    group_id: UUID, payload: ExpenseCreate, user: CurrentUser, session: SessionDep
) -> ExpenseOut:
    group = await _load_group(session, group_id, user)
    member_ids = {m.id for m in await _members(session, group.id)}
    currency = payload.currency or group.currency
    if currency != group.currency:
        raise DomainValidationError(
            f"Expense currency must match group currency ({group.currency})", code="currency_mismatch"
        )
    if payload.paid_by_member_id not in member_ids:
        raise DomainValidationError("Payer is not a member of this group", code="invalid_member")
    participant_ids = [p.member_id for p in payload.participants]
    if not set(participant_ids) <= member_ids:
        raise DomainValidationError("Participant is not a member of this group", code="invalid_member")
    if len(set(participant_ids)) != len(participant_ids):
        raise DomainValidationError("Duplicate participants")

    percents = {p.member_id: p.percent for p in payload.participants if p.percent is not None}
    match payload.split_type:
        case SplitType.equal:
            shares = split_math.split_equal(payload.amount_minor, participant_ids)
        case SplitType.exact:
            shares = split_math.split_exact(
                payload.amount_minor,
                {p.member_id: p.amount_minor or 0 for p in payload.participants},
            )
        case SplitType.percent:
            shares = split_math.split_percent(payload.amount_minor, percents)

    expense = SplitExpense(
        group_id=group.id,
        paid_by_member_id=payload.paid_by_member_id,
        created_by=user.id,
        description=payload.description,
        amount_minor=payload.amount_minor,
        currency=currency,
        split_type=payload.split_type,
        spent_at=payload.spent_at or datetime.now(UTC),
        notes=payload.notes,
    )
    session.add(expense)
    await session.flush()
    share_rows = [
        SplitShare(
            expense_id=expense.id,
            member_id=m,
            share_minor=amt,
            percent=percents.get(m) if payload.split_type is SplitType.percent else None,
        )
        for m, amt in shares.items()
    ]
    session.add_all(share_rows)
    await session.commit()
    await session.refresh(expense)
    return _expense_out(expense, share_rows)


@router.get("/groups/{group_id}/expenses", response_model=list[ExpenseOut])
async def list_expenses(
    group_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ExpenseOut]:
    group = await _load_group(session, group_id, user)
    expenses = list(
        (
            await session.execute(
                select(SplitExpense)
                .where(SplitExpense.group_id == group.id, SplitExpense.deleted_at.is_(None))
                .order_by(SplitExpense.spent_at.desc(), SplitExpense.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    shares = (
        await session.execute(select(SplitShare).where(SplitShare.expense_id.in_([e.id for e in expenses])))
    ).scalars()
    by_expense: dict[UUID, list[SplitShare]] = {}
    for s in shares:
        by_expense.setdefault(s.expense_id, []).append(s)
    return [_expense_out(e, by_expense.get(e.id, [])) for e in expenses]


@router.delete("/groups/{group_id}/expenses/{expense_id}", status_code=204)
async def delete_expense(
    group_id: UUID, expense_id: UUID, user: CurrentUser, session: SessionDep
) -> Response:
    group = await _load_group(session, group_id, user)
    expense = (
        await session.execute(
            select(SplitExpense).where(
                SplitExpense.id == expense_id,
                SplitExpense.group_id == group.id,
                SplitExpense.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if expense is None:
        raise NotFoundError("Expense not found")
    expense.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.post("/groups/{group_id}/settlements", response_model=SettlementOut, status_code=201)
async def create_settlement(
    group_id: UUID, payload: SettlementCreate, user: CurrentUser, session: SessionDep
) -> Settlement:
    group = await _load_group(session, group_id, user)
    member_ids = {m.id for m in await _members(session, group.id)}
    if payload.from_member_id not in member_ids or payload.to_member_id not in member_ids:
        raise DomainValidationError("Settlement members must belong to the group", code="invalid_member")
    settlement = Settlement(
        group_id=group.id,
        created_by=user.id,
        from_member_id=payload.from_member_id,
        to_member_id=payload.to_member_id,
        amount_minor=payload.amount_minor,
        currency=group.currency,
        settled_at=payload.settled_at or datetime.now(UTC),
        note=payload.note,
    )
    session.add(settlement)
    await session.commit()
    await session.refresh(settlement)
    return settlement


@router.get("/groups/{group_id}/settlements", response_model=list[SettlementOut])
async def list_settlements(group_id: UUID, user: CurrentUser, session: SessionDep) -> list[Settlement]:
    group = await _load_group(session, group_id, user)
    rows = await session.execute(
        select(Settlement)
        .where(Settlement.group_id == group.id, Settlement.deleted_at.is_(None))
        .order_by(Settlement.settled_at.desc())
    )
    return list(rows.scalars())


@router.get("/groups/{group_id}/balances", response_model=GroupBalancesOut)
async def group_balances(group_id: UUID, user: CurrentUser, session: SessionDep) -> GroupBalancesOut:
    group = await _load_group(session, group_id, user)
    members = await _members(session, group.id)
    expenses = list(
        (
            await session.execute(
                select(SplitExpense).where(
                    SplitExpense.group_id == group.id, SplitExpense.deleted_at.is_(None)
                )
            )
        ).scalars()
    )
    shares_by_expense: dict[UUID, dict[UUID, int]] = {e.id: {} for e in expenses}
    if expenses:
        for s in (
            await session.execute(
                select(SplitShare).where(SplitShare.expense_id.in_(shares_by_expense.keys()))
            )
        ).scalars():
            shares_by_expense[s.expense_id][s.member_id] = s.share_minor
    settlements = (
        await session.execute(
            select(Settlement).where(Settlement.group_id == group.id, Settlement.deleted_at.is_(None))
        )
    ).scalars()

    balances = split_math.compute_balances(
        [
            split_math.ExpenseRecord(e.paid_by_member_id, e.amount_minor, shares_by_expense[e.id])
            for e in expenses
        ],
        [split_math.Transfer(s.from_member_id, s.to_member_id, s.amount_minor) for s in settlements],
        members=[m.id for m in members],
    )
    transfers = split_math.simplify_debts(balances)
    return GroupBalancesOut(
        currency=group.currency,
        balances=[
            BalanceOut(member_id=m.id, display_name=m.display_name, net_minor=balances.get(m.id, 0))
            for m in members
        ],
        transfers=[
            TransferOut(from_member_id=t.from_member, to_member_id=t.to_member, amount_minor=t.amount_minor)
            for t in transfers
        ],
    )

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import DomainValidationError, ForbiddenError, NotFoundError
from app.models.split import (
    Settlement,
    SettlementMethod,
    SettlementStatus,
    SplitExpense,
    SplitGroup,
    SplitMember,
    SplitShare,
    SplitType,
)
from app.models.user import User
from app.schemas.split import (
    AaniRequestIn,
    AaniRequestOut,
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
from app.services.phone import InvalidPhoneError, normalise_phone, phone_hash
from app.services.settlements import (
    Parties,
    allowed_actions,
    confirm,
    format_amount,
    initial_status,
    mark_paid,
    may_cancel,
    share_text,
)

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
    e164 = p_hash = None
    if m.phone and m.phone.strip():
        try:
            e164 = normalise_phone(m.phone)
        except InvalidPhoneError as exc:
            raise DomainValidationError(str(exc), code="invalid_phone") from exc
        p_hash = phone_hash(e164)
        dup_phone = await session.execute(
            select(SplitMember.id).where(SplitMember.group_id == group_id, SplitMember.phone_hash == p_hash)
        )
        if dup_phone.scalar_one_or_none() is not None:
            raise DomainValidationError("This number is already in the group", code="duplicate_member")
    user_id = m.user_id
    if user_id is None and p_hash is not None:
        # Link to a registered, discoverable Pulse user holding this number (unless already a member).
        found = (
            await session.execute(
                select(User.id).where(
                    User.phone_hash == p_hash, User.phone_discoverable.is_(True), User.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            already = await session.execute(
                select(SplitMember.id).where(SplitMember.group_id == group_id, SplitMember.user_id == found)
            )
            if already.scalar_one_or_none() is not None:
                raise DomainValidationError("This person is already in the group", code="duplicate_member")
            user_id = found
    if m.user_id is not None:
        if await session.get(User, m.user_id) is None:
            raise DomainValidationError("Unknown user_id", code="invalid_member")
        dup = await session.execute(
            select(SplitMember.id).where(SplitMember.group_id == group_id, SplitMember.user_id == m.user_id)
        )
        if dup.scalar_one_or_none() is not None:
            raise DomainValidationError("User is already a member", code="duplicate_member")
    member = SplitMember(
        group_id=group_id,
        display_name=m.display_name,
        email=m.email,
        user_id=user_id,
        phone_e164=e164,
        phone_hash=p_hash,
    )
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


def _parties(s: Settlement, members: dict[UUID, SplitMember]) -> Parties:
    return Parties(members[s.from_member_id].user_id, members[s.to_member_id].user_id, s.created_by)


def _settlement_out(s: Settlement, members: dict[UUID, SplitMember], user: User) -> SettlementOut:
    out = SettlementOut.model_validate(s)
    for k, v in allowed_actions(s, _parties(s, members), user.id).items():
        setattr(out, k, v)
    return out


async def _member_map(session: AsyncSession, group_id: UUID) -> dict[UUID, SplitMember]:
    return {m.id: m for m in await _members(session, group_id)}


async def _load_settlement(session: AsyncSession, group_id: UUID, settlement_id: UUID) -> Settlement:
    s = (
        await session.execute(
            select(Settlement).where(
                Settlement.id == settlement_id,
                Settlement.group_id == group_id,
                Settlement.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if s is None:
        raise NotFoundError("Settlement not found")
    return s


@router.post("/groups/{group_id}/settlements", response_model=SettlementOut, status_code=201)
async def create_settlement(
    group_id: UUID, payload: SettlementCreate, user: CurrentUser, session: SessionDep
) -> SettlementOut:
    group = await _load_group(session, group_id, user)
    members = await _member_map(session, group.id)
    if payload.from_member_id not in members or payload.to_member_id not in members:
        raise DomainValidationError("Settlement members must belong to the group", code="invalid_member")
    if payload.method is SettlementMethod.aani and group.currency != "AED":
        raise DomainValidationError("Aani settles in AED only", code="aani_requires_aed")
    now = datetime.now(UTC)
    status = initial_status(payload.method)
    settlement = Settlement(
        group_id=group.id,
        created_by=user.id,
        from_member_id=payload.from_member_id,
        to_member_id=payload.to_member_id,
        amount_minor=payload.amount_minor,
        currency=group.currency,
        settled_at=payload.settled_at or now,
        note=payload.note,
        method=payload.method,
        status=status,
        confirmed_at=now if status is SettlementStatus.confirmed else None,
        confirmed_by=user.id if status is SettlementStatus.confirmed else None,
    )
    session.add(settlement)
    await session.commit()
    await session.refresh(settlement)
    return _settlement_out(settlement, members, user)


@router.post("/groups/{group_id}/settlements/aani-request", response_model=AaniRequestOut, status_code=201)
async def create_aani_request(
    group_id: UUID, payload: AaniRequestIn, user: CurrentUser, session: SessionDep
) -> AaniRequestOut:
    """Start an Aani settlement: returns what the payer needs to pay in their bank app (amount, payee name,
    payee mobile, share text) and creates a *pending* settlement. Aani has no public API, so after paying
    the payer calls `mark-paid`, and the payee calls `confirm`."""
    group = await _load_group(session, group_id, user)
    if group.currency != "AED":
        raise DomainValidationError("Aani settles in AED only", code="aani_requires_aed")
    members = await _member_map(session, group.id)
    me = next((m for m in members.values() if m.user_id == user.id), None)
    from_id = payload.from_member_id or (me.id if me else None)
    if from_id is None or from_id not in members or payload.to_member_id not in members:
        raise DomainValidationError("Settlement members must belong to the group", code="invalid_member")
    if from_id == payload.to_member_id:
        raise DomainValidationError("Payer and payee must differ", code="invalid_member")
    payee = members[payload.to_member_id]
    parties = Parties(members[from_id].user_id, payee.user_id, user.id)
    if not parties.payer_acts(user.id):
        raise ForbiddenError("You can only request an Aani payment you are making", code="not_payer")
    phone = payee.phone_e164
    if payee.user_id is not None:
        payee_user = await session.get(User, payee.user_id)
        if payee_user is not None:
            phone = payee_user.aani_phone_e164 or payee_user.phone_e164 or phone
    if not phone:
        raise DomainValidationError(
            f"No mobile number for {payee.display_name}: add one to the member, or ask them to set their "
            "Aani mobile in their profile",
            code="payee_phone_missing",
        )
    now = datetime.now(UTC)
    settlement = Settlement(
        group_id=group.id,
        created_by=user.id,
        from_member_id=from_id,
        to_member_id=payee.id,
        amount_minor=payload.amount_minor,
        currency=group.currency,
        settled_at=now,
        note=payload.note,
        method=SettlementMethod.aani,
        status=SettlementStatus.pending,
    )
    session.add(settlement)
    await session.commit()
    await session.refresh(settlement)
    amount = format_amount(payload.amount_minor, group.currency)
    return AaniRequestOut(
        settlement=_settlement_out(settlement, members, user),
        amount_minor=payload.amount_minor,
        currency=group.currency,
        amount_display=amount,
        payee_display_name=payee.display_name,
        payee_phone=phone,
        share_text=share_text(payee.display_name, payload.amount_minor, group.currency, group.name, phone),
        instructions=[
            "Open your bank app and choose Aani / pay by mobile number.",
            f"Send {amount} to {phone} ({payee.display_name}).",
            "Come back to Pulse and tap 'I've paid'. The payee then confirms.",
        ],
    )


@router.post("/groups/{group_id}/settlements/{settlement_id}/mark-paid", response_model=SettlementOut)
async def mark_settlement_paid(
    group_id: UUID, settlement_id: UUID, user: CurrentUser, session: SessionDep
) -> SettlementOut:
    """Payer: 'I paid' (idempotent). The settlement stays pending until the payee confirms."""
    group = await _load_group(session, group_id, user)
    members = await _member_map(session, group.id)
    s = await _load_settlement(session, group.id, settlement_id)
    mark_paid(s, _parties(s, members), user.id, datetime.now(UTC))
    await session.commit()
    await session.refresh(s)
    return _settlement_out(s, members, user)


@router.post("/groups/{group_id}/settlements/{settlement_id}/confirm", response_model=SettlementOut)
async def confirm_settlement(
    group_id: UUID, settlement_id: UUID, user: CurrentUser, session: SessionDep
) -> SettlementOut:
    """Payee: the money arrived. The settlement now counts towards balances."""
    group = await _load_group(session, group_id, user)
    members = await _member_map(session, group.id)
    s = await _load_settlement(session, group.id, settlement_id)
    confirm(s, _parties(s, members), user.id, datetime.now(UTC))
    await session.commit()
    await session.refresh(s)
    return _settlement_out(s, members, user)


@router.delete("/groups/{group_id}/settlements/{settlement_id}", status_code=204)
async def cancel_settlement(
    group_id: UUID, settlement_id: UUID, user: CurrentUser, session: SessionDep
) -> Response:
    """Cancel a settlement that is still pending (payer, payee or creator)."""
    group = await _load_group(session, group_id, user)
    members = await _member_map(session, group.id)
    s = await _load_settlement(session, group.id, settlement_id)
    may_cancel(s, _parties(s, members), user.id)
    s.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.get("/groups/{group_id}/settlements", response_model=list[SettlementOut])
async def list_settlements(
    group_id: UUID, user: CurrentUser, session: SessionDep, status: SettlementStatus | None = None
) -> list[SettlementOut]:
    group = await _load_group(session, group_id, user)
    members = await _member_map(session, group.id)
    stmt = select(Settlement).where(Settlement.group_id == group.id, Settlement.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Settlement.status == status)
    rows = await session.execute(stmt.order_by(Settlement.settled_at.desc()))
    return [_settlement_out(s, members, user) for s in rows.scalars()]


@router.get("/settlements/awaiting-confirmation", response_model=list[SettlementOut])
async def settlements_awaiting_my_confirmation(user: CurrentUser, session: SessionDep) -> list[SettlementOut]:
    """Pending settlements, across all groups, that the current user can confirm as the payee."""
    rows = await session.execute(
        select(Settlement)
        .where(
            Settlement.deleted_at.is_(None),
            Settlement.status == SettlementStatus.pending,
            exists().where(SplitMember.group_id == Settlement.group_id, SplitMember.user_id == user.id),
        )
        .order_by(Settlement.created_at.desc())
    )
    out: list[SettlementOut] = []
    cache: dict[UUID, dict[UUID, SplitMember]] = {}
    for s in rows.scalars():
        members = cache.setdefault(s.group_id, await _member_map(session, s.group_id))
        if _parties(s, members).payee_acts(user.id):
            out.append(_settlement_out(s, members, user))
    return out


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
            select(Settlement).where(
                Settlement.group_id == group.id,
                Settlement.deleted_at.is_(None),
                Settlement.status == SettlementStatus.confirmed,
            )
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

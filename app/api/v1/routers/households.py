from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import ConflictError, DomainValidationError, ForbiddenError, NotFoundError
from app.models.household import Household, HouseholdMember, HouseholdRole, MemberStatus, ShareLevel
from app.models.user import User
from app.schemas.household import (
    CategoryAmountOut,
    HouseholdCreate,
    HouseholdOut,
    HouseholdSummaryOut,
    HouseholdTotalsOut,
    HouseholdUpdate,
    InvitationOut,
    InviteIn,
    MemberOut,
    MemberOverviewOut,
    MemberTxnOut,
    OverviewOut,
    RoleIn,
    SharingIn,
)
from app.services import family
from app.services.audit import record_audit
from app.services.family_data import history_start, member_data, member_transactions
from app.services.insights_service import today_dubai
from app.services.phone import InvalidPhoneError, normalise_phone, phone_hash

router = APIRouter(prefix="/households", tags=["family"])
ACTIVE = MemberStatus.active


async def _membership(
    session: AsyncSession, household_id: UUID, user_id: UUID
) -> tuple[Household, HouseholdMember]:
    """The household and the caller's active seat in it; 404 for anyone else (no probing)."""
    row = (
        await session.execute(
            select(Household, HouseholdMember)
            .join(HouseholdMember, HouseholdMember.household_id == Household.id)
            .where(
                Household.id == household_id,
                Household.deleted_at.is_(None),
                HouseholdMember.user_id == user_id,
                HouseholdMember.status == ACTIVE,
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Household not found")
    return row[0], row[1]


async def _members(session: AsyncSession, household_id: UUID) -> list[HouseholdMember]:
    rows = await session.execute(
        select(HouseholdMember)
        .where(
            HouseholdMember.household_id == household_id,
            HouseholdMember.status.in_((MemberStatus.active, MemberStatus.invited)),
        )
        .order_by(HouseholdMember.created_at)
    )
    return list(rows.scalars())


def _member_out(m: HouseholdMember, me: UUID) -> MemberOut:
    return MemberOut(
        id=m.id,
        display_name=m.display_name,
        role=m.role,
        status=m.status,
        is_you=m.user_id == me,
        share_level=m.share_level,
        invited_via=(family.mask_phone(m.invite_phone_e164) or m.invite_email)
        if m.status is MemberStatus.invited
        else None,
        joined_at=m.joined_at,
    )


async def _household_out(
    session: AsyncSession, h: Household, mine: HouseholdMember, me: UUID
) -> HouseholdOut:
    return HouseholdOut(
        id=h.id,
        name=h.name,
        your_role=mine.role,
        your_member_id=mine.id,
        members=[_member_out(m, me) for m in await _members(session, h.id)],
    )


@router.post("", response_model=HouseholdOut, status_code=201)
async def create_household(payload: HouseholdCreate, user: CurrentUser, session: SessionDep) -> HouseholdOut:
    h = Household(name=payload.name, created_by=user.id)
    session.add(h)
    await session.flush()
    owner = HouseholdMember(
        household_id=h.id,
        user_id=user.id,
        display_name=payload.display_name or user.display_name or "Me",
        role=HouseholdRole.owner,
        status=ACTIVE,
        share_level=ShareLevel.nothing,
        joined_at=datetime.now(UTC),
    )
    session.add(owner)
    await session.commit()
    return await _household_out(session, h, owner, user.id)


@router.get("", response_model=list[HouseholdSummaryOut])
async def my_households(user: CurrentUser, session: SessionDep) -> list[HouseholdSummaryOut]:
    rows = (
        await session.execute(
            select(Household, HouseholdMember)
            .join(HouseholdMember, HouseholdMember.household_id == Household.id)
            .where(
                Household.deleted_at.is_(None),
                HouseholdMember.user_id == user.id,
                HouseholdMember.status == ACTIVE,
            )
            .order_by(Household.created_at)
        )
    ).all()
    out = []
    for h, m in rows:
        count = len([x for x in await _members(session, h.id) if x.status is ACTIVE])
        out.append(HouseholdSummaryOut(id=h.id, name=h.name, your_role=m.role, member_count=count))
    return out


@router.get("/invitations", response_model=list[InvitationOut])
async def my_invitations(user: CurrentUser, session: SessionDep) -> list[InvitationOut]:
    """Pending invites addressed to you (by account, your verified phone, or your email)."""
    match = [HouseholdMember.user_id == user.id]
    if user.phone_hash:
        match.append(HouseholdMember.invite_phone_hash == user.phone_hash)
    if user.email:
        match.append(HouseholdMember.invite_email == user.email.lower())
    rows = (
        await session.execute(
            select(Household, HouseholdMember)
            .join(HouseholdMember, HouseholdMember.household_id == Household.id)
            .where(
                Household.deleted_at.is_(None), HouseholdMember.status == MemberStatus.invited, or_(*match)
            )
        )
    ).all()
    out = []
    for h, m in rows:
        inviter = await session.get(User, m.invited_by) if m.invited_by else None
        out.append(
            InvitationOut(
                household_id=h.id,
                household_name=h.name,
                member_id=m.id,
                role=m.role,
                invited_by_name=inviter.display_name if inviter else None,
            )
        )
    return out


async def _my_invite(session: AsyncSession, household_id: UUID, user: User) -> HouseholdMember:
    match = [HouseholdMember.user_id == user.id]
    if user.phone_hash:
        match.append(HouseholdMember.invite_phone_hash == user.phone_hash)
    if user.email:
        match.append(HouseholdMember.invite_email == user.email.lower())
    m = (
        (
            await session.execute(
                select(HouseholdMember)
                .join(Household, Household.id == HouseholdMember.household_id)
                .where(
                    HouseholdMember.household_id == household_id,
                    HouseholdMember.status == MemberStatus.invited,
                    Household.deleted_at.is_(None),
                    or_(*match),
                )
            )
        )
        .scalars()
        .first()
    )
    if m is None:
        raise NotFoundError("No pending invitation")
    return m


@router.post("/{household_id}/invitations/accept", response_model=HouseholdOut)
async def accept_invitation(household_id: UUID, user: CurrentUser, session: SessionDep) -> HouseholdOut:
    m = await _my_invite(session, household_id, user)
    already = (
        (
            await session.execute(
                select(HouseholdMember).where(
                    HouseholdMember.household_id == household_id,
                    HouseholdMember.user_id == user.id,
                    HouseholdMember.id != m.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if already is not None:
        await session.delete(already)  # an old removed/declined seat gives way to the new one
        await session.flush()
    m.user_id, m.status, m.joined_at, m.share_level = user.id, ACTIVE, datetime.now(UTC), ShareLevel.nothing
    await session.commit()
    h, mine = await _membership(session, household_id, user.id)
    return await _household_out(session, h, mine, user.id)


@router.post("/{household_id}/invitations/decline", status_code=204)
async def decline_invitation(household_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    m = await _my_invite(session, household_id, user)
    m.status = MemberStatus.declined
    await session.commit()
    return Response(status_code=204)


@router.get("/{household_id}", response_model=HouseholdOut)
async def get_household(household_id: UUID, user: CurrentUser, session: SessionDep) -> HouseholdOut:
    h, mine = await _membership(session, household_id, user.id)
    return await _household_out(session, h, mine, user.id)


@router.patch("/{household_id}", response_model=HouseholdOut)
async def rename_household(
    household_id: UUID, payload: HouseholdUpdate, user: CurrentUser, session: SessionDep
) -> HouseholdOut:
    h, mine = await _membership(session, household_id, user.id)
    if mine.role is not HouseholdRole.owner:
        raise ForbiddenError("Only the owner can rename the household")
    h.name = payload.name
    await session.commit()
    return await _household_out(session, h, mine, user.id)


@router.delete("/{household_id}", status_code=204)
async def delete_household(household_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    h, mine = await _membership(session, household_id, user.id)
    if mine.role is not HouseholdRole.owner:
        raise ForbiddenError("Only the owner can delete the household")
    h.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.post("/{household_id}/invitations", response_model=MemberOut, status_code=201)
async def invite(household_id: UUID, payload: InviteIn, user: CurrentUser, session: SessionDep) -> MemberOut:
    """Invite by phone or email. The response never says whether the invitee already has an account."""
    h, mine = await _membership(session, household_id, user.id)
    if not family.can_invite(mine.role):
        raise ForbiddenError("Dependents cannot invite")
    phone_e164 = phone_h = email = None
    target: User | None
    if payload.phone:
        try:
            phone_e164 = normalise_phone(payload.phone)
        except InvalidPhoneError as exc:
            raise DomainValidationError(str(exc), code="invalid_phone") from exc
        phone_h = phone_hash(phone_e164)
        target = (
            (await session.execute(select(User).where(User.phone_hash == phone_h, User.deleted_at.is_(None))))
            .scalars()
            .first()
        )
    else:
        email = str(payload.email).lower()
        target = (
            (await session.execute(select(User).where(User.email.ilike(email), User.deleted_at.is_(None))))
            .scalars()
            .first()
        )
    if target is not None and target.id == user.id:
        raise DomainValidationError("You are already in this household", code="invalid_invite")
    rows = await session.execute(select(HouseholdMember).where(HouseholdMember.household_id == h.id))
    for existing in rows.scalars():
        same = (
            (target is not None and existing.user_id == target.id)
            or (phone_h and existing.invite_phone_hash == phone_h)
            or (email and existing.invite_email == email)
        )
        if not same:
            continue
        if existing.status in (MemberStatus.active, MemberStatus.invited):
            raise ConflictError("Already invited or a member", code="already_invited")
        existing.status, existing.role = MemberStatus.invited, HouseholdRole(payload.role)  # re-invite
        existing.display_name, existing.invited_by = payload.display_name, user.id
        existing.share_level, existing.joined_at = ShareLevel.nothing, None
        await session.commit()
        return _member_out(existing, user.id)
    m = HouseholdMember(
        household_id=h.id,
        user_id=target.id if target else None,
        display_name=payload.display_name,
        role=HouseholdRole(payload.role),
        status=MemberStatus.invited,
        share_level=ShareLevel.nothing,
        invite_phone_e164=phone_e164,
        invite_phone_hash=phone_h,
        invite_email=email,
        invited_by=user.id,
    )
    session.add(m)
    await session.commit()
    return _member_out(m, user.id)


async def _target(session: AsyncSession, household_id: UUID, member_id: UUID) -> HouseholdMember:
    m = (
        await session.execute(
            select(HouseholdMember).where(
                HouseholdMember.id == member_id, HouseholdMember.household_id == household_id
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFoundError("Member not found")
    return m


@router.patch("/{household_id}/members/me/sharing", response_model=MemberOut)
async def set_my_sharing(
    household_id: UUID, payload: SharingIn, user: CurrentUser, session: SessionDep
) -> MemberOut:
    """Only you can change what you share."""
    _, mine = await _membership(session, household_id, user.id)
    mine.share_level = payload.share_level
    await session.commit()
    return _member_out(mine, user.id)


@router.patch("/{household_id}/members/{member_id}", response_model=MemberOut)
async def set_member_role(
    household_id: UUID, member_id: UUID, payload: RoleIn, user: CurrentUser, session: SessionDep
) -> MemberOut:
    _, mine = await _membership(session, household_id, user.id)
    if mine.role is not HouseholdRole.owner:
        raise ForbiddenError("Only the owner can change roles")
    m = await _target(session, household_id, member_id)
    if m.role is HouseholdRole.owner:
        raise DomainValidationError("The owner's role cannot be changed", code="invalid_role")
    m.role = HouseholdRole(payload.role)
    await session.commit()
    return _member_out(m, user.id)


@router.delete("/{household_id}/members/{member_id}", status_code=204)
async def remove_member(
    household_id: UUID, member_id: UUID, user: CurrentUser, session: SessionDep
) -> Response:
    """The owner removes anyone (or cancels an invite); any member can leave. The owner cannot leave."""
    _, mine = await _membership(session, household_id, user.id)
    m = await _target(session, household_id, member_id)
    if m.role is HouseholdRole.owner:
        raise DomainValidationError(
            "The owner cannot leave: delete the household instead", code="owner_cannot_leave"
        )
    if mine.role is not HouseholdRole.owner and m.id != mine.id:
        raise ForbiddenError("Only the owner can remove other members")
    m.status, m.share_level = MemberStatus.removed, ShareLevel.nothing
    await session.commit()
    return Response(status_code=204)


def _parse_month(raw: str | None) -> date:
    if raw is None:
        return today_dubai().replace(day=1)
    try:
        y, mo = raw.split("-")
        return date(int(y), int(mo), 1)
    except (ValueError, TypeError) as exc:
        raise DomainValidationError("month must look like 2026-03", code="invalid_month") from exc


@router.get("/{household_id}/overview", response_model=OverviewOut)
async def overview(
    household_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    month: Annotated[str | None, Query(description="YYYY-MM, default this month")] = None,
) -> OverviewOut:
    """Each member as they chose to be seen, and household totals built only from members who share."""
    h, mine = await _membership(session, household_id, user.id)
    first = _parse_month(month)
    members: list[MemberOverviewOut] = []
    included = hidden = cat_included = 0
    income = spend = net_worth = 0
    cat_lists: list[list[family.CategoryTotal]] = []
    for m in [x for x in await _members(session, h.id) if x.status is ACTIVE and x.user_id]:
        assert m.user_id is not None
        is_self = m.user_id == user.id
        level = family.visible_level(mine.role, is_self, m.share_level)
        data = await member_data(session, m.user_id, first, level)
        members.append(
            MemberOverviewOut(
                member_id=m.id,
                display_name=m.display_name,
                role=m.role,
                is_you=is_self,
                shares=m.share_level,
                you_can_see=level,
                data=data,
            )
        )
        if data is None:  # level is "nothing": not in the totals
            hidden += 1
            continue
        included += 1
        income += data.income_minor
        spend += data.spend_minor
        net_worth += data.net_worth_minor
        if data.categories is not None:
            cat_included += 1
            cat_lists.append(
                [
                    family.CategoryTotal(c.slug or c.name, c.name, c.slug, c.total_minor)
                    for c in data.categories
                ]
            )
    merged = family.merge_categories(cat_lists)
    return OverviewOut(
        household_id=h.id,
        name=h.name,
        month=first,
        currency="AED",
        members=members,
        household=HouseholdTotalsOut(
            members_included=included,
            members_not_shared=hidden,
            income_minor=income,
            spend_minor=spend,
            net_minor=income - spend,
            net_worth_minor=net_worth,
            category_members_included=cat_included,
            categories=[
                CategoryAmountOut(name=c.name, slug=c.slug, total_minor=c.total_minor) for c in merged
            ],
        ),
        notes=[
            "Totals include only members who share at least totals; categories need that level.",
            "Each member controls what they share and can change it at any time.",
        ],
    )


@router.get("/{household_id}/members/{member_id}/transactions", response_model=list[MemberTxnOut])
async def member_txns(
    household_id: UUID,
    member_id: UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[MemberTxnOut]:
    """A member's transactions, only if they share 'full' with you (or it is you). Every view is audited."""
    _, mine = await _membership(session, household_id, user.id)
    m = await _target(session, household_id, member_id)
    if m.user_id is None or m.status is not ACTIVE:
        raise NotFoundError("Member not found")
    level = family.visible_level(mine.role, m.user_id == user.id, m.share_level)
    if not family.at_least(level, ShareLevel.full):
        raise ForbiddenError("This member does not share their transactions", code="not_shared")
    if m.user_id != user.id:
        record_audit(
            session,
            user_id=user.id,
            action="household.view_transactions",
            entity_type="household_member",
            entity_id=m.id,
            request_id=getattr(request.state, "request_id", None),
            data={"household_id": str(household_id), "viewed_user_id": str(m.user_id)},
        )
        await session.commit()
    return await member_transactions(session, m.user_id, history_start(today_dubai(), days), limit)

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import get_owned
from app.core.errors import DomainValidationError, NotFoundError
from app.models.account import Account, AccountType
from app.models.card import CatalogCard, UserCard
from app.schemas.insights import CardDueOut, DueDatesOut
from app.schemas.rewards import CatalogCardOut, UserCardCreate, UserCardOut
from app.services import card_intel
from app.services import insights_service as insights
from app.services.rewards_service import catalog_out

router = APIRouter(prefix="/cards", tags=["cards"])


def _out(uc: UserCard, cat: CatalogCard) -> UserCardOut:
    return UserCardOut(
        id=uc.id,
        created_at=uc.created_at,
        updated_at=uc.updated_at,
        catalog_card_id=uc.catalog_card_id,
        account_id=uc.account_id,
        nickname=uc.nickname,
        card=catalog_out(cat),
    )


@router.get("/catalog", response_model=list[CatalogCardOut])
async def card_catalog(user: CurrentUser, session: SessionDep) -> list[CatalogCardOut]:
    """Reference card products with reward rules. `data_status` is 'unverified' until `verified_on` is set."""
    rows = await session.execute(select(CatalogCard).order_by(CatalogCard.issuer, CatalogCard.name))
    return [catalog_out(c) for c in rows.scalars()]


@router.get("/intel")
async def card_intel_feed(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Tips, caveats, stacking combos and offer changes matched to the user's cards (curated, dated)."""
    rows = await session.execute(
        select(CatalogCard.perks)
        .join(UserCard, UserCard.catalog_card_id == CatalogCard.id)
        .where(UserCard.user_id == user.id, UserCard.deleted_at.is_(None))
    )
    owned = {str(p["catalog_id"]) for (p,) in rows if p and p.get("catalog_id")}
    intel = card_intel.load_intel()
    today = insights.today_dubai()
    return {
        "as_of": intel["as_of"],
        "items": [r.out() for r in card_intel.intel_for_cards(intel, owned, today)],
    }


@router.get("/combos")
async def card_combos(user: CurrentUser) -> dict[str, Any]:
    """Default stacking combos and tips for everyone, with or without cards. Each item carries a `tier`
    ("free" or "plus"); the app locks plus items until the user is entitled (docs/ENTITLEMENT.md)."""
    intel = card_intel.load_intel()
    today = insights.today_dubai()
    return {
        "as_of": intel["as_of"],
        "items": [r.out() for r in card_intel.starter_items(intel, today)],
    }


@router.get("/due-dates", response_model=DueDatesOut)
async def card_due_dates(user: CurrentUser, session: SessionDep) -> DueDatesOut:
    """Per credit card: statement date, due date with days left, minimum due and total due (separately,
    net of payments since the statement), utilisation and pay-by nudges."""
    today = insights.today_dubai()
    rows = await insights.card_dues(session, user.id, today)
    return DueDatesOut(as_of=today, cards=[CardDueOut(**asdict(d)) for d in rows])


@router.get("", response_model=list[UserCardOut])
async def my_cards(user: CurrentUser, session: SessionDep) -> list[UserCardOut]:
    rows = await session.execute(
        select(UserCard, CatalogCard)
        .join(CatalogCard, CatalogCard.id == UserCard.catalog_card_id)
        .where(UserCard.user_id == user.id, UserCard.deleted_at.is_(None))
        .order_by(UserCard.created_at)
    )
    return [_out(uc, cat) for uc, cat in rows]


@router.post("", response_model=UserCardOut, status_code=201)
async def add_card(payload: UserCardCreate, user: CurrentUser, session: SessionDep) -> UserCardOut:
    cat = await session.get(CatalogCard, payload.catalog_card_id)
    if cat is None:
        raise DomainValidationError("Unknown catalog card", code="invalid_card")
    if payload.account_id is not None:
        acc = await get_owned(session, Account, payload.account_id, user.id)
        if acc.type is not AccountType.credit_card:
            raise DomainValidationError("Link a credit-card account", code="invalid_account")
    uc = UserCard(user_id=user.id, **payload.model_dump())
    session.add(uc)
    await session.commit()
    await session.refresh(uc)
    return _out(uc, cat)


@router.delete("/{card_id}", status_code=204)
async def remove_card(card_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    uc = (
        await session.execute(
            select(UserCard).where(
                UserCard.id == card_id, UserCard.user_id == user.id, UserCard.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if uc is None:
        raise NotFoundError("Card not found")
    uc.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)

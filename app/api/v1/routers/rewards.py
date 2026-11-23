from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.schemas.rewards import BestCardIn, BestCardOut, BestCardRow, RewardsSummaryOut
from app.services.parsers.dates import to_dubai_date
from app.services.rewards_service import (
    category_slugs,
    load_cards,
    rank_cards,
    resolve_target,
    summarise_card,
)

router = APIRouter(prefix="/rewards", tags=["rewards"])


@router.get("/summary", response_model=RewardsSummaryOut)
async def rewards_summary(user: CurrentUser, session: SessionDep) -> RewardsSummaryOut:
    """Per card: earned this cycle, cap used, min-spend and fee-waiver progress, cycle dates."""
    today = to_dubai_date(datetime.now(UTC))
    slugs = await category_slugs(session, user.id)
    cards = [
        await summarise_card(session, user.id, c, today, slugs) for c in await load_cards(session, user.id)
    ]
    return RewardsSummaryOut(
        as_of=today, cards=cards, total_earned_value_minor=sum(c.earned_value_minor for c in cards)
    )


@router.post("/best-card", response_model=BestCardOut)
async def best_card(payload: BestCardIn, user: CurrentUser, session: SessionDep) -> BestCardOut:
    """Which of the user's cards earns most for this purchase, given this cycle's earnings."""
    today = to_dubai_date(datetime.now(UTC))
    slugs = await category_slugs(session, user.id)
    category, mkey = resolve_target(payload.category, payload.merchant)
    ranked = await rank_cards(
        session,
        user.id,
        await load_cards(session, user.id),
        category,
        mkey,
        payload.amount_minor,
        today,
        slugs,
    )
    return BestCardOut(
        category=category,
        merchant=payload.merchant,
        amount_minor=payload.amount_minor,
        ranked=[
            BestCardRow(
                user_card_id=c.user_card.id,
                name=c.title,
                rule=q.rule,
                estimated_units=q.units.quantize(Decimal("0.01")),
                estimated_value_minor=q.value_minor,
                effective_rate_pct=(Decimal(q.value_minor) / payload.amount_minor * 100).quantize(
                    Decimal("0.01")
                ),
                cap_headroom_units=q.headroom_units.quantize(Decimal("0.01"))
                if q.headroom_units is not None
                else None,
                hits_cap=q.capped,
                data_status="verified" if c.catalog.verified_at else "unverified",
            )
            for c, q in ranked
        ],
    )

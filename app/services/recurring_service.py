"""Database side of recurring detection: load charges, apply reviews, merge with loan EMIs."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Liability
from app.models.insights import RecurringReview
from app.models.transaction import Direction, Transaction
from app.schemas.recurring import (
    PriceIncreaseOut,
    RecurringListOut,
    RecurringOut,
    UpcomingItem,
)
from app.services import recurring as rc
from app.services.merchants import merchant_key
from app.services.parsers.dates import to_dubai_date
from app.services.rewards_service import category_slugs

LOOKBACK_DAYS = 548  # ~18 months: enough for two yearly charges


@dataclass(slots=True)
class Detected:
    series: list[rc.Series]
    reviews: dict[str, str]


async def detect_for_user(session: AsyncSession, user_id: UUID, today: date) -> Detected:
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    slugs = await category_slugs(session, user_id)
    rows = await session.execute(
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.direction == Direction.debit,
            Transaction.currency == "AED",
            Transaction.posted_at >= since,
        )
        .order_by(Transaction.posted_at)
    )
    charges: list[rc.Charge] = []
    for t in rows.scalars():
        name = t.merchant_normalized or t.merchant_raw
        key = merchant_key(name)
        if not key or not name:
            continue
        charges.append(
            rc.Charge(
                to_dubai_date(t.posted_at),
                t.amount_minor,
                name.strip(),
                key,
                slugs.get(t.category_id) if t.category_id else None,
            )
        )
    reviews = {
        r.series_key: r.status
        for r in (
            await session.execute(select(RecurringReview).where(RecurringReview.user_id == user_id))
        ).scalars()
    }
    return Detected(rc.detect(charges, today), reviews)


def to_out(s: rc.Series, review: str | None, today: date) -> RecurringOut:
    flags = list(s.flags)
    if rc.forgotten(s, review == "confirmed"):
        flags.append("forgotten_subscription")
    if s.status == "lapsed":
        flags.append("lapsed")
    return RecurringOut(
        key=s.key,
        merchant=s.merchant,
        cadence=s.cadence,
        amount_kind=s.amount_kind,
        typical_amount_minor=s.typical_amount_minor,
        last_amount_minor=s.last_amount_minor,
        monthly_cost_minor=rc.monthly_cost_minor(s),
        last_charge_on=s.last_charge_on,
        next_expected_on=s.next_expected_on,
        days_until_next=(s.next_expected_on - today).days,
        status=s.status,
        occurrences=s.occurrences,
        category_slug=s.category_slug,
        is_subscription=s.is_subscription,
        flags=flags,
        price_increase=PriceIncreaseOut(**s.price_increase) if s.price_increase else None,
        review=review,
    )


def build_list(det: Detected, today: date, include_dismissed: bool = False) -> RecurringListOut:
    visible = [s for s in det.series if include_dismissed or det.reviews.get(s.key) != "dismissed"]
    outs = [to_out(s, det.reviews.get(s.key), today) for s in visible]
    live = [(s, o) for s, o in zip(visible, outs, strict=True) if s.status != "lapsed"]
    return RecurringListOut(
        as_of=today,
        series=outs,
        monthly_total_minor=sum(o.monthly_cost_minor for _, o in live),
        subscriptions_monthly_minor=sum(o.monthly_cost_minor for s, o in live if s.is_subscription),
        price_increases=[o for o in outs if "price_increase" in o.flags and o.status != "lapsed"],
        forgotten_subscriptions=[o for o in outs if "forgotten_subscription" in o.flags],
    )


async def upcoming_items(
    session: AsyncSession, user_id: UUID, today: date, end: date, det: Detected | None = None
) -> list[UpcomingItem]:
    """Recurring charges and loan EMIs expected in [today, end]. A loan with a due date replaces the
    detected series it matches (same amount within 2%) so the EMI is never counted twice."""
    det = det or await detect_for_user(session, user_id, today)
    liabilities = [
        li
        for li in (
            await session.execute(
                select(Liability).where(Liability.user_id == user_id, Liability.deleted_at.is_(None))
            )
        ).scalars()
        if li.emi_minor and li.next_due_on and li.currency == "AED"
    ]
    items: list[UpcomingItem] = []
    covered: set[str] = set()
    for li in liabilities:
        assert li.emi_minor is not None and li.next_due_on is not None
        d = li.next_due_on
        anchor = d.day
        while d < today:
            d = rc.add_months(d, 1, anchor)
        while d <= end and (li.end_date is None or d <= li.end_date):
            items.append(
                UpcomingItem(
                    date=d, name=li.name, amount_minor=li.emi_minor, kind="loan", variable_amount=False
                )
            )
            d = rc.add_months(d, 1, anchor)
        for s in det.series:
            if s.cadence == "monthly" and abs(s.typical_amount_minor - li.emi_minor) <= li.emi_minor * 0.02:
                covered.add(s.key)
    for s in det.series:
        if s.key in covered or det.reviews.get(s.key) == "dismissed":
            continue
        for d in rc.occurrences_between(s, today, end):
            items.append(
                UpcomingItem(
                    date=d,
                    name=s.merchant,
                    amount_minor=s.typical_amount_minor,
                    kind="recurring",
                    variable_amount=s.amount_kind == "variable",
                    key=s.key,
                )
            )
    items.sort(key=lambda i: (i.date, i.name))
    return items

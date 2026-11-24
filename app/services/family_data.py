"""What a household member's finances look like to the rest of the household (computed live, read-only)."""

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.household import ShareLevel
from app.models.transaction import Transaction
from app.schemas.household import CategoryAmountOut, MemberDataOut, MemberTxnOut
from app.services import cashflow as cf
from app.services.family import at_least
from app.services.insights_service import day_start, load_txns, snapshot_values


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


async def member_data(
    session: AsyncSession, user_id: UUID, month: date, level: ShareLevel
) -> MemberDataOut | None:
    """The member's month at the given visibility level (None for nothing)."""
    if level is ShareLevel.nothing:
        return None
    end = next_month(month)
    txns = [t for t in await load_txns(session, user_id, month) if month <= t.day < end]
    income = sum(t.amount_minor for t in txns if cf.is_income(t.flow()))
    spend_txns = [t for t in txns if cf.is_spend(t.flow())]
    spend = sum(t.amount_minor for t in spend_txns)
    assets, liabilities, _ = await snapshot_values(session, user_id)
    categories: list[CategoryAmountOut] | None = None
    if at_least(level, ShareLevel.categories):
        cats = {
            c.id: c
            for c in (
                await session.execute(
                    select(Category).where((Category.user_id.is_(None)) | (Category.user_id == user_id))
                )
            ).scalars()
        }
        totals: dict[UUID | None, int] = defaultdict(int)
        for t in spend_txns:
            c = cats.get(t.category_id) if t.category_id else None
            while c is not None and c.parent_id is not None and c.parent_id in cats:
                c = cats[c.parent_id]
            totals[c.id if c else None] += t.amount_minor
        categories = sorted(
            (
                CategoryAmountOut(
                    name=cats[cid].name if cid else "Uncategorised",
                    slug=cats[cid].slug if cid else None,
                    total_minor=amt,
                )
                for cid, amt in totals.items()
            ),
            key=lambda c: -c.total_minor,
        )
    return MemberDataOut(
        income_minor=income,
        spend_minor=spend,
        net_minor=income - spend,
        net_worth_minor=assets - liabilities,
        categories=categories,
        transactions_available=level is ShareLevel.full,
    )


async def member_transactions(
    session: AsyncSession, user_id: UUID, since: date, limit: int
) -> list[MemberTxnOut]:
    """Recent transactions without notes, tags or account details."""
    cats = {
        c.id: c.name
        for c in (
            await session.execute(
                select(Category).where((Category.user_id.is_(None)) | (Category.user_id == user_id))
            )
        ).scalars()
    }
    rows = await session.execute(
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.posted_at >= day_start(since),
        )
        .order_by(Transaction.posted_at.desc())
        .limit(limit)
    )
    return [
        MemberTxnOut(
            id=t.id,
            posted_at=t.posted_at,
            amount_minor=t.amount_minor,
            direction=t.direction.value,
            merchant=t.merchant_normalized or t.merchant_raw,
            category=cats.get(t.category_id) if t.category_id else None,
        )
        for t in rows.scalars()
    ]


def history_start(today: date, days: int = 90) -> date:
    return today - timedelta(days=days)

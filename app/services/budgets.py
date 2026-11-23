"""Budget maths: spend per category family, month-to-month rollover and the all-categories overview."""

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.transaction import Direction, Transaction
from app.schemas.budget import BudgetOut
from app.services.categories import descendant_ids

MAX_ROLLOVER_MONTHS = 12


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def prev_month(d: date) -> date:
    return date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)


async def spent_in_month(
    session: AsyncSession, user_id: UUID, category_ids: list[UUID], month: date, currency: str
) -> int:
    """Debits in the month for the given categories (budget-excluded transactions do not count)."""
    spent = (
        await session.execute(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.user_id == user_id,
                Transaction.deleted_at.is_(None),
                Transaction.category_id.in_(category_ids),
                Transaction.direction == Direction.debit,
                Transaction.currency == currency,
                Transaction.posted_at >= month,
                Transaction.posted_at < next_month(month),
            )
        )
    ).scalar_one()
    return int(spent)


async def _available(session: AsyncSession, b: Budget, family: list[UUID], depth: int = 0) -> tuple[int, int]:
    """(available, carried_in). A rollover budget inherits last month's unspent amount, recursively
    through consecutive rollover months (bounded), never a negative carry."""
    carry = 0
    if b.rollover and depth < MAX_ROLLOVER_MONTHS:
        prev = (
            await session.execute(
                select(Budget).where(
                    Budget.user_id == b.user_id,
                    Budget.category_id == b.category_id,
                    Budget.month == prev_month(b.month),
                    Budget.currency == b.currency,
                )
            )
        ).scalar_one_or_none()
        if prev is not None:
            prev_available, _ = await _available(session, prev, family, depth + 1)
            prev_spent = await spent_in_month(session, b.user_id, family, prev.month, prev.currency)
            carry = max(0, prev_available - prev_spent)
    return b.limit_minor + carry, carry


async def budget_out(session: AsyncSession, b: Budget) -> BudgetOut:
    family = await descendant_ids(session, b.category_id)
    available, carry = await _available(session, b, family)
    spent = await spent_in_month(session, b.user_id, family, b.month, b.currency)
    out = BudgetOut.model_validate(b)
    out.rollover_in_minor = carry
    out.available_minor = available
    out.spent_minor = spent
    out.remaining_minor = available - spent
    return out

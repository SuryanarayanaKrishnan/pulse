from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import get_owned
from app.core.errors import ConflictError
from app.models.budget import Budget
from app.models.category import CategoryKind
from app.models.transaction import Direction, Transaction
from app.schemas.budget import (
    BudgetCreate,
    BudgetOut,
    BudgetOverview,
    BudgetOverviewRow,
    BudgetUpdate,
)
from app.services.budgets import budget_out, next_month
from app.services.categories import category_out, get_visible_category, load_preferences, visible_categories

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetOut])
async def list_budgets(
    user: CurrentUser,
    session: SessionDep,
    month: Annotated[date | None, Query(description="Any date in the month")] = None,
) -> list[BudgetOut]:
    stmt = select(Budget).where(Budget.user_id == user.id).order_by(Budget.month.desc())
    if month:
        stmt = stmt.where(Budget.month == month.replace(day=1))
    rows = list((await session.execute(stmt)).scalars())
    return [await budget_out(session, b) for b in rows]


@router.get("/overview", response_model=BudgetOverview)
async def budget_overview(
    user: CurrentUser,
    session: SessionDep,
    month: Annotated[date | None, Query(description="Any date in the month; default: current month")] = None,
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")] = "AED",
    include_hidden: bool = False,
) -> BudgetOverview:
    """Every expense category (defaults + custom) with its budget for the month and what was spent.
    A parent category's `spent_minor` includes its sub-categories."""
    first = (month or date.today()).replace(day=1)
    categories = [c for c in await visible_categories(session, user.id) if c.kind == CategoryKind.expense]
    prefs = await load_preferences(session, user.id)
    spent_rows = await session.execute(
        select(Transaction.category_id, func.sum(Transaction.amount_minor))
        .where(
            Transaction.user_id == user.id,
            Transaction.deleted_at.is_(None),
            Transaction.direction == Direction.debit,
            Transaction.currency == currency,
            Transaction.posted_at >= first,
            Transaction.posted_at < next_month(first),
            Transaction.category_id.is_not(None),
        )
        .group_by(Transaction.category_id)
    )
    own_spend = {cid: int(total) for cid, total in spent_rows if cid is not None}
    parent_of = {c.id: c.parent_id for c in categories}
    spend = dict(own_spend)
    for cid, total in own_spend.items():
        parent = parent_of.get(cid)
        if parent is not None:
            spend[parent] = spend.get(parent, 0) + total
    budgets = {
        b.category_id: b
        for b in (
            await session.execute(
                select(Budget).where(
                    Budget.user_id == user.id, Budget.month == first, Budget.currency == currency
                )
            )
        ).scalars()
    }
    rows: list[BudgetOverviewRow] = []
    for c in categories:
        out = category_out(c, prefs.get(c.id))
        if out.is_hidden and not include_hidden:
            continue
        b = budgets.get(c.id)
        rows.append(
            BudgetOverviewRow(
                category=out,
                budget=await budget_out(session, b) if b else None,
                spent_minor=spend.get(c.id, 0),
            )
        )
    return BudgetOverview(
        month=first,
        currency=currency,
        rows=rows,
        total_limit_minor=sum(b.limit_minor for b in budgets.values()),
        total_spent_minor=sum(r.spent_minor for r in rows if r.category.parent_id is None),
    )


@router.post("", response_model=BudgetOut, status_code=201)
async def create_budget(payload: BudgetCreate, user: CurrentUser, session: SessionDep) -> BudgetOut:
    await get_visible_category(session, user.id, payload.category_id)
    budget = Budget(user_id=user.id, **payload.model_dump())
    session.add(budget)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A budget for this category and month already exists") from exc
    await session.refresh(budget)
    return await budget_out(session, budget)


@router.put("", response_model=BudgetOut)
async def upsert_budget(payload: BudgetCreate, user: CurrentUser, session: SessionDep) -> BudgetOut:
    """Set the budget for a category and month, creating or replacing it (what a budget screen calls)."""
    await get_visible_category(session, user.id, payload.category_id)
    existing = (
        await session.execute(
            select(Budget).where(
                Budget.user_id == user.id,
                Budget.category_id == payload.category_id,
                Budget.month == payload.month,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = Budget(user_id=user.id, **payload.model_dump())
        session.add(existing)
    else:
        existing.limit_minor = payload.limit_minor
        existing.currency = payload.currency
        existing.rollover = payload.rollover
    await session.commit()
    await session.refresh(existing)
    return await budget_out(session, existing)


@router.patch("/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: UUID, payload: BudgetUpdate, user: CurrentUser, session: SessionDep
) -> BudgetOut:
    budget = await get_owned(session, Budget, budget_id, user.id)
    if payload.limit_minor is not None:
        budget.limit_minor = payload.limit_minor
    if payload.rollover is not None:
        budget.rollover = payload.rollover
    await session.commit()
    await session.refresh(budget)
    return await budget_out(session, budget)


@router.delete("/{budget_id}", status_code=204)
async def delete_budget(budget_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    budget = await get_owned(session, Budget, budget_id, user.id)
    await session.delete(budget)
    await session.commit()
    return Response(status_code=204)

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import get_owned
from app.core.errors import ConflictError, DomainValidationError
from app.models.budget import Budget
from app.models.category import Category
from app.models.transaction import Direction, Transaction
from app.schemas.budget import (
    BudgetCreate,
    BudgetOut,
    BudgetUpdate,
    CategoryCreate,
    CategoryOut,
)

router = APIRouter(tags=["budgets"])


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, session: SessionDep) -> list[Category]:
    rows = await session.execute(
        select(Category)
        .where(or_(Category.user_id.is_(None), Category.user_id == user.id))
        .order_by(Category.kind, Category.name)
    )
    return list(rows.scalars())


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(payload: CategoryCreate, user: CurrentUser, session: SessionDep) -> Category:
    cat = Category(user_id=user.id, **payload.model_dump())
    session.add(cat)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Category already exists") from exc
    await session.refresh(cat)
    return cat


async def _with_spend(session: SessionDep, user_id: UUID, budgets: list[Budget]) -> list[BudgetOut]:
    out: list[BudgetOut] = []
    for b in budgets:
        spent = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                    Transaction.user_id == user_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.category_id == b.category_id,
                    Transaction.direction == Direction.debit,
                    Transaction.currency == b.currency,
                    Transaction.posted_at >= b.month,
                    Transaction.posted_at < _next_month(b.month),
                )
            )
        ).scalar_one()
        item = BudgetOut.model_validate(b)
        item.spent_minor = int(spent)
        item.remaining_minor = b.limit_minor - int(spent)
        out.append(item)
    return out


@router.get("/budgets", response_model=list[BudgetOut])
async def list_budgets(
    user: CurrentUser,
    session: SessionDep,
    month: Annotated[date | None, Query(description="Any date in the month")] = None,
) -> list[BudgetOut]:
    stmt = select(Budget).where(Budget.user_id == user.id).order_by(Budget.month.desc())
    if month:
        stmt = stmt.where(Budget.month == month.replace(day=1))
    rows = list((await session.execute(stmt)).scalars())
    return await _with_spend(session, user.id, rows)


@router.post("/budgets", response_model=BudgetOut, status_code=201)
async def create_budget(payload: BudgetCreate, user: CurrentUser, session: SessionDep) -> BudgetOut:
    cat = await session.get(Category, payload.category_id)
    if cat is None or cat.user_id not in (None, user.id):
        raise DomainValidationError("Unknown category", code="invalid_category")
    budget = Budget(user_id=user.id, **payload.model_dump())
    session.add(budget)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A budget for this category and month already exists") from exc
    await session.refresh(budget)
    return (await _with_spend(session, user.id, [budget]))[0]


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
async def update_budget(
    budget_id: UUID, payload: BudgetUpdate, user: CurrentUser, session: SessionDep
) -> BudgetOut:
    budget = await get_owned(session, Budget, budget_id, user.id)
    budget.limit_minor = payload.limit_minor
    await session.commit()
    await session.refresh(budget)
    return (await _with_spend(session, user.id, [budget]))[0]


@router.delete("/budgets/{budget_id}", status_code=204)
async def delete_budget(budget_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    budget = await get_owned(session, Budget, budget_id, user.id)
    await session.delete(budget)
    await session.commit()
    return Response(status_code=204)

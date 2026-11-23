from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates, get_owned
from app.models.insights import SavingsGoal
from app.schemas.goal import GoalCreate, GoalOut, GoalUpdate
from app.services import goals as g
from app.services.parsers.dates import to_dubai_date

router = APIRouter(prefix="/goals", tags=["goals"])


def goal_out(goal: SavingsGoal) -> GoalOut:
    today = to_dubai_date(datetime.now(UTC))
    remaining = max(0, goal.target_minor - goal.saved_minor)
    return GoalOut(
        id=goal.id,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        name=goal.name,
        target_minor=goal.target_minor,
        saved_minor=goal.saved_minor,
        currency=goal.currency,
        target_date=goal.target_date,
        monthly_contribution_minor=goal.monthly_contribution_minor,
        remaining_minor=remaining,
        progress_pct=round(min(100.0, goal.saved_minor / goal.target_minor * 100), 1),
        months_left=g.months_left(today, goal.target_date),
        required_monthly_minor=g.required_monthly(remaining, today, goal.target_date),
        monthly_reserve_minor=g.monthly_reserve(
            remaining, goal.monthly_contribution_minor, today, goal.target_date
        ),
        achieved=remaining == 0,
    )


@router.get("", response_model=list[GoalOut])
async def list_goals(user: CurrentUser, session: SessionDep) -> list[GoalOut]:
    rows = await session.execute(
        select(SavingsGoal)
        .where(SavingsGoal.user_id == user.id, SavingsGoal.deleted_at.is_(None))
        .order_by(SavingsGoal.created_at)
    )
    return [goal_out(x) for x in rows.scalars()]


@router.post("", response_model=GoalOut, status_code=201)
async def create_goal(payload: GoalCreate, user: CurrentUser, session: SessionDep) -> GoalOut:
    goal = SavingsGoal(user_id=user.id, currency="AED", **payload.model_dump())
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal_out(goal)


@router.get("/{goal_id}", response_model=GoalOut)
async def get_goal(goal_id: UUID, user: CurrentUser, session: SessionDep) -> GoalOut:
    return goal_out(await get_owned(session, SavingsGoal, goal_id, user.id))


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(goal_id: UUID, payload: GoalUpdate, user: CurrentUser, session: SessionDep) -> GoalOut:
    goal = await get_owned(session, SavingsGoal, goal_id, user.id)
    nullable = {"target_date", "monthly_contribution_minor"}
    data = payload.model_dump(exclude_unset=True)
    apply_updates(goal, {k: v for k, v in data.items() if v is not None or k in nullable})
    await session.commit()
    await session.refresh(goal)
    return goal_out(goal)


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(goal_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    goal = await get_owned(session, SavingsGoal, goal_id, user.id)
    goal.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)

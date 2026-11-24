from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import delete, select

from app.api.deps import CurrentUser, SessionDep
from app.models.insights import RecurringReview
from app.schemas.recurring import RecurringListOut, ReviewIn, UpcomingOut
from app.services.parsers.dates import to_dubai_date
from app.services.recurring_service import build_list, detect_for_user, upcoming_items

router = APIRouter(prefix="/recurring", tags=["recurring"])


@router.get("", response_model=RecurringListOut)
async def recurring(
    user: CurrentUser, session: SessionDep, include_dismissed: bool = False
) -> RecurringListOut:
    """Recurring payments and subscriptions found in the user's spending, with price-increase and
    forgotten-subscription flags. Detection needs 3 charges (2 for yearly) at a steady cadence."""
    today = to_dubai_date(datetime.now(UTC))
    return build_list(await detect_for_user(session, user.id, today), today, include_dismissed)


@router.get("/upcoming", response_model=UpcomingOut)
async def upcoming(
    user: CurrentUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> UpcomingOut:
    today = to_dubai_date(datetime.now(UTC))
    end = today + timedelta(days=days)
    items = await upcoming_items(session, user.id, today, end)
    return UpcomingOut(
        from_date=today, to_date=end, items=items, total_minor=sum(i.amount_minor for i in items)
    )


@router.put("/{key:path}/review", status_code=204)
async def review(key: str, payload: ReviewIn, user: CurrentUser, session: SessionDep) -> Response:
    """Keep ('confirmed': stops the forgotten-subscription flag) or hide ('dismissed') a series."""
    existing = (
        await session.execute(
            select(RecurringReview).where(
                RecurringReview.user_id == user.id, RecurringReview.series_key == key
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.status = payload.status
    else:
        session.add(RecurringReview(user_id=user.id, series_key=key[:160], status=payload.status))
    await session.commit()
    return Response(status_code=204)


@router.delete("/{key:path}/review", status_code=204)
async def clear_review(key: str, user: CurrentUser, session: SessionDep) -> Response:
    await session.execute(
        delete(RecurringReview).where(RecurringReview.user_id == user.id, RecurringReview.series_key == key)
    )
    await session.commit()
    return Response(status_code=204)

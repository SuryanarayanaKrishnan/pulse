from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models.insights import NetWorthSnapshot
from app.schemas.documents import RemindersOut
from app.schemas.insights import (
    CashflowOut,
    CommitmentOut,
    HealthComponentOut,
    HealthScoreOut,
    MonthCashflowOut,
    NetWorthHistoryOut,
    NetWorthPointOut,
    ProjectedDayOut,
    ProjectedEventOut,
    ProjectionOut,
    SafeToSpendOut,
    SpendTrendsOut,
)
from app.services import insights_service as svc

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/cashflow", response_model=CashflowOut)
async def cashflow(
    user: CurrentUser,
    session: SessionDep,
    months: Annotated[int, Query(ge=1, le=36)] = 12,
    projection_days: Annotated[int, Query(ge=1, le=120)] = 30,
) -> CashflowOut:
    """Income, spend and savings rate per month, plus a day-by-day projection of the liquid balance."""
    today = svc.today_dubai()
    monthly, proj = await svc.cashflow_report(session, user.id, today, months, projection_days)
    days = proj.pop("days", [])
    return CashflowOut(
        currency="AED",
        months=[
            MonthCashflowOut(
                month=m.month, income_minor=m.income_minor, spend_minor=m.spend_minor,
                saved_minor=m.saved_minor, net_minor=m.net_minor, savings_rate=m.savings_rate,
            )
            for m in monthly
        ],
        projection=ProjectionOut(
            **proj,
            days=[
                ProjectedDayOut(
                    date=d.day, balance_minor=d.balance_minor,
                    events=[
                        ProjectedEventOut(label=e.label, amount_minor=e.amount_minor, kind=e.kind)
                        for e in d.events
                    ],
                )
                for d in days
            ],
        ),
    )  # fmt: skip


@router.get("/spend-trends", response_model=SpendTrendsOut)
async def spend_trends(
    user: CurrentUser,
    session: SessionDep,
    months: Annotated[int, Query(ge=2, le=24)] = 6,
    top: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SpendTrendsOut:
    """Spend by category by month (sub-categories roll up to their parent) and top merchants."""
    data = await svc.spend_trends(session, user.id, svc.today_dubai(), months, top)
    return SpendTrendsOut(currency="AED", **data)


@router.get("/net-worth-history", response_model=NetWorthHistoryOut)
async def net_worth_history(
    user: CurrentUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=3650)] = 365,
) -> NetWorthHistoryOut:
    """Daily net-worth snapshots. Today's is refreshed on every call; history starts at the first snapshot."""
    today = svc.today_dubai()
    await svc.take_snapshot(session, user.id, today)
    rows = (
        await session.execute(
            select(NetWorthSnapshot)
            .where(
                NetWorthSnapshot.user_id == user.id,
                NetWorthSnapshot.snapshot_date >= today - timedelta(days=days),
            )
            .order_by(NetWorthSnapshot.snapshot_date)
        )
    ).scalars()
    points = [
        NetWorthPointOut(
            date=r.snapshot_date, assets_minor=r.assets_minor, liabilities_minor=r.liabilities_minor,
            net_worth_minor=r.net_worth_minor,
        )
        for r in rows
    ]  # fmt: skip
    return NetWorthHistoryOut(
        currency="AED",
        points=points,
        change_minor=points[-1].net_worth_minor - points[0].net_worth_minor if len(points) > 1 else None,
        note="Snapshots start the first time you open this; earlier days are not reconstructed.",
    )


@router.get("/safe-to-spend", response_model=SafeToSpendOut)
async def safe_to_spend(user: CurrentUser, session: SessionDep) -> SafeToSpendOut:
    """Liquid balance minus upcoming bills, card dues and goal contributions until the next salary
    (or month end), with the breakdown."""
    today = svc.today_dubai()
    result, end, basis, notes = await svc.safe_to_spend(session, user.id, today)
    return SafeToSpendOut(
        currency="AED", as_of=today, until=end, until_basis=basis, days_left=result.days_left,
        status=result.status,
        liquid_minor=result.liquid_minor, committed_minor=result.committed_minor,
        safe_to_spend_minor=result.safe_minor, per_day_minor=result.per_day_minor, by_kind=result.by_kind,
        breakdown=[
            CommitmentOut(kind=c.kind, label=c.label, amount_minor=c.amount_minor, due_on=c.due_on)
            for c in result.commitments
        ],
        notes=notes,
    )  # fmt: skip


@router.get("/reminders", response_model=RemindersOut)
async def reminders(
    user: CurrentUser, session: SessionDep, days: Annotated[int, Query(ge=1, le=730)] = 90
) -> RemindersOut:
    """Maturity, renewal and expiry dates from your documents and fixed-deposit assets: overdue first,
    then what falls due within the window. `next_due` is the single most urgent open item."""
    today = svc.today_dubai()
    items = await svc.due_items(session, user.id, today, days)
    return RemindersOut(as_of=today, next_due=items[0] if items else None, items=items)


@router.get("/health-score", response_model=HealthScoreOut)
async def health_score(user: CurrentUser, session: SessionDep) -> HealthScoreOut:
    """0-100 score from savings rate, credit utilisation, on-time card payments and emergency runway."""
    h = await svc.health_score(session, user.id, svc.today_dubai())
    return HealthScoreOut(
        score=h.score, band=h.band, coverage=h.coverage, summary=h.summary,
        components=[
            HealthComponentOut(key=c.key, weight=c.weight, score=c.score, value=c.value, detail=c.detail)
            for c in h.components
        ],
    )  # fmt: skip

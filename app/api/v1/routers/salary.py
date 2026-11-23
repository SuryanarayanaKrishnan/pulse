from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import get_owned
from app.models.budget import SalaryRecord
from app.schemas.salary import (
    DetectOut,
    EmployerOut,
    MonthlyTotal,
    SalaryRecordOut,
    SalarySummary,
)
from app.services.parsers.dates import to_dubai_date
from app.services.salary import Payment, rescan, summarise, user_records

router = APIRouter(prefix="/salary", tags=["salary"])


@router.get("", response_model=SalarySummary)
async def salary_summary(
    user: CurrentUser,
    session: SessionDep,
    months: Annotated[int, Query(ge=1, le=60, description="How many months of records to return")] = 12,
) -> SalarySummary:
    """Detected salary credits and the recurring-salary summary (AED unless the user's salary is not)."""
    records = await user_records(session, user.id)
    today = to_dubai_date(datetime.now(UTC))
    s = summarise([Payment(r.received_on, r.amount_minor, r.employer, r.currency) for r in records], today)
    in_currency = [r for r in records if r.currency == s.currency]
    window = s.monthly[:months]
    cutoff = window[-1][0] if window else None
    shown = [r for r in in_currency if cutoff is None or r.received_on >= cutoff]
    return SalarySummary(
        detected=bool(records),
        currency=s.currency,
        is_recurring=s.is_recurring,
        months_observed=s.months_observed,
        latest=SalaryRecordOut.model_validate(in_currency[0]) if in_currency else None,
        latest_month_minor=s.latest_month_minor,
        previous_month_minor=s.previous_month_minor,
        change_minor=(
            s.latest_month_minor - s.previous_month_minor
            if s.latest_month_minor is not None and s.previous_month_minor is not None
            else None
        ),
        average_monthly_minor=s.average_monthly_minor,
        typical_day=s.typical_day,
        next_expected_on=s.next_expected_on,
        monthly=[MonthlyTotal(month=m, amount_minor=t) for m, t in window],
        employers=[EmployerOut(employer=e, payments=n, latest_amount_minor=a) for e, n, a in s.employers],
        records=[SalaryRecordOut.model_validate(r) for r in shown],
    )


@router.post("/detect", response_model=DetectOut)
async def detect_salary_in_history(user: CurrentUser, session: SessionDep) -> DetectOut:
    """Re-scan past credits for salary payments (new ones are also detected as they arrive)."""
    scanned, created = await rescan(session, user.id)
    await session.commit()
    return DetectOut(scanned=scanned, detected=created)


@router.delete("/records/{record_id}", status_code=204)
async def dismiss_salary_record(record_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    """'This is not a salary': hides the record and stops it being detected again."""
    record = await get_owned(session, SalaryRecord, record_id, user.id)
    record.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)

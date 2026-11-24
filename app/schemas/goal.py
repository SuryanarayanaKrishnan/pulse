from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import MinorAmount, PositiveMinor, Timestamps


class GoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_minor: PositiveMinor
    saved_minor: MinorAmount = 0
    target_date: date | None = None
    monthly_contribution_minor: MinorAmount | None = Field(
        default=None,
        description="What you plan to set aside monthly; derived from the target date if omitted",
    )


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target_minor: PositiveMinor | None = None
    saved_minor: MinorAmount | None = None
    target_date: date | None = None
    monthly_contribution_minor: MinorAmount | None = None


class GoalOut(Timestamps):
    name: str
    target_minor: int
    saved_minor: int
    currency: str
    target_date: date | None
    monthly_contribution_minor: int | None
    remaining_minor: int
    progress_pct: float
    months_left: int | None
    required_monthly_minor: int | None = Field(description="Needed each month to hit the target date")
    monthly_reserve_minor: int = Field(description="Amount set aside for this goal each month")
    achieved: bool

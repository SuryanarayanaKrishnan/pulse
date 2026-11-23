from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.category import CategoryOut
from app.schemas.common import Currency, MinorAmount, Timestamps


class BudgetCreate(BaseModel):
    category_id: UUID
    month: date = Field(description="Any date in the month; normalised to the 1st")
    limit_minor: MinorAmount
    currency: Currency = "AED"
    rollover: bool = Field(default=False, description="Carry last month's unspent amount into this month")

    @field_validator("month")
    @classmethod
    def _normalise_month(cls, v: date) -> date:
        return v.replace(day=1)


class BudgetUpdate(BaseModel):
    limit_minor: MinorAmount | None = None
    rollover: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "BudgetUpdate":
        if self.limit_minor is None and self.rollover is None:
            raise ValueError("Provide limit_minor and/or rollover")
        return self


class BudgetOut(Timestamps):
    category_id: UUID
    month: date
    limit_minor: int
    currency: str
    rollover: bool = False
    rollover_in_minor: int = Field(default=0, description="Unspent amount carried in from last month")
    available_minor: int = Field(default=0, description="limit + rollover_in")
    spent_minor: int = 0
    remaining_minor: int = Field(default=0, description="available - spent (negative when over budget)")


class BudgetOverviewRow(BaseModel):
    category: CategoryOut
    budget: BudgetOut | None
    spent_minor: int = Field(description="Debits in the month for the category and its sub-categories")


class BudgetOverview(BaseModel):
    month: date
    currency: str
    rows: list[BudgetOverviewRow]
    total_limit_minor: int
    total_spent_minor: int

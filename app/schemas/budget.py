from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.category import CategoryKind
from app.schemas.common import Currency, MinorAmount, ORMModel, Timestamps


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: CategoryKind = CategoryKind.expense
    parent_id: UUID | None = None
    icon: str | None = Field(default=None, max_length=40)


class CategoryOut(ORMModel):
    id: UUID
    user_id: UUID | None
    name: str
    kind: CategoryKind
    parent_id: UUID | None
    icon: str | None


class BudgetCreate(BaseModel):
    category_id: UUID
    month: date = Field(description="Any date in the month; normalised to the 1st")
    limit_minor: MinorAmount
    currency: Currency = "AED"

    @field_validator("month")
    @classmethod
    def _first_of_month(cls, v: date) -> date:
        return v.replace(day=1)


class BudgetUpdate(BaseModel):
    limit_minor: MinorAmount


class BudgetOut(Timestamps):
    category_id: UUID
    month: date
    limit_minor: int
    currency: str
    spent_minor: int = 0
    remaining_minor: int = 0

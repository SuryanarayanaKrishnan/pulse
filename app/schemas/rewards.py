from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel, PositiveMinor, Timestamps


class CatalogCardOut(BaseModel):
    id: UUID
    issuer: str
    name: str
    network: str | None
    annual_fee_minor: int
    min_salary_minor: int | None
    reward_structure: dict[str, Any]
    perks: dict[str, Any]
    verified_on: date | None = Field(description="When the reward terms were last checked against the issuer")
    source: str | None = Field(description="Where the terms came from")
    data_status: str = Field(
        description="'verified' once verified_on is set, else 'unverified' (best-effort)"
    )


class UserCardCreate(BaseModel):
    catalog_card_id: UUID
    account_id: UUID | None = Field(default=None, description="The credit-card account whose spend earns")
    nickname: str | None = Field(default=None, max_length=80)


class UserCardOut(Timestamps):
    catalog_card_id: UUID
    account_id: UUID | None
    nickname: str | None
    card: CatalogCardOut


class CapOut(BaseModel):
    rule: str
    cap_units: Decimal | None
    used_units: Decimal
    remaining_units: Decimal | None
    pct_used: Decimal | None


class FeeWaiverOut(BaseModel):
    annual_fee_minor: int
    threshold_minor: int
    spend_this_year_minor: int
    progress_pct: Decimal
    waived: bool


class CardRewardsOut(BaseModel):
    user_card_id: UUID
    name: str
    issuer: str
    nickname: str | None
    account_id: UUID | None
    unit: str
    cycle_start: date
    cycle_end: date
    days_left: int
    spend_minor: int
    earned_units: Decimal
    earned_value_minor: int = Field(description="AED value earned this cycle (0 until min spend is met)")
    pending_value_minor: int = Field(description="Value earned but not payable until min spend is met")
    min_spend_required_minor: int
    min_spend_met: bool
    caps: list[CapOut]
    fee_waiver: FeeWaiverOut | None
    data_status: str
    verified_on: date | None
    source: str | None


class RewardsSummaryOut(BaseModel):
    as_of: date
    cards: list[CardRewardsOut]
    total_earned_value_minor: int


class BestCardIn(BaseModel):
    category: str | None = Field(default=None, max_length=40, description="Category slug, e.g. dining")
    merchant: str | None = Field(default=None, max_length=200)
    amount_minor: PositiveMinor

    @model_validator(mode="after")
    def _one_of(self) -> "BestCardIn":
        if not (self.category or self.merchant):
            raise ValueError("Provide category and/or merchant")
        return self


class BestCardRow(BaseModel):
    user_card_id: UUID
    name: str
    rule: str | None
    estimated_units: Decimal
    estimated_value_minor: int
    effective_rate_pct: Decimal
    cap_headroom_units: Decimal | None
    hits_cap: bool
    data_status: str


class BestCardOut(BaseModel):
    category: str | None
    merchant: str | None
    amount_minor: int
    ranked: list[BestCardRow]


class Nudge(ORMModel):
    level: str
    message: str
    card_id: UUID
    suggested_card_id: UUID | None
    extra_value_minor: int
    created_at: datetime | None = None

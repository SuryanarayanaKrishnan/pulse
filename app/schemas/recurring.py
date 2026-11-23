from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class PriceIncreaseOut(BaseModel):
    from_minor: int
    to_minor: int
    increase_minor: int
    pct: float


class RecurringOut(BaseModel):
    key: str = Field(description="Stable id of the series; use it with PUT /recurring/{key}/review")
    merchant: str
    cadence: str = Field(description="weekly | fortnightly | monthly | quarterly | yearly")
    amount_kind: str = Field(description="fixed (same amount each time) or variable (e.g. utilities)")
    typical_amount_minor: int
    last_amount_minor: int
    monthly_cost_minor: int = Field(description="Cost normalised to a month")
    last_charge_on: date
    next_expected_on: date
    days_until_next: int
    status: str = Field(description="active | due (expected, not seen yet) | lapsed (stopped)")
    occurrences: int
    category_slug: str | None
    is_subscription: bool
    flags: list[str] = Field(description="price_increase | forgotten_subscription | lapsed")
    price_increase: PriceIncreaseOut | None
    review: str | None = Field(description="confirmed | dismissed | null")


class RecurringListOut(BaseModel):
    as_of: date
    series: list[RecurringOut]
    monthly_total_minor: int = Field(description="Active recurring spend per month (all series)")
    subscriptions_monthly_minor: int
    price_increases: list[RecurringOut]
    forgotten_subscriptions: list[RecurringOut]


class UpcomingItem(BaseModel):
    date: date
    name: str
    amount_minor: int
    kind: Literal["recurring", "loan"]
    variable_amount: bool = Field(description="True when the amount is an estimate (variable bills)")
    key: str | None = None


class UpcomingOut(BaseModel):
    from_date: date
    to_date: date
    items: list[UpcomingItem]
    total_minor: int


class ReviewIn(BaseModel):
    status: Literal["confirmed", "dismissed"]

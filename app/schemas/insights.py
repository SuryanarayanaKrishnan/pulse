from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class MonthCashflowOut(BaseModel):
    month: date = Field(description="First day of the month")
    income_minor: int
    spend_minor: int
    saved_minor: int = Field(description="Moved to savings/investments (not counted as spend)")
    net_minor: int = Field(description="income minus spend")
    savings_rate: float | None = Field(description="net / income; null when there was no income")


class ProjectedEventOut(BaseModel):
    label: str
    amount_minor: int = Field(description="Negative = money out")
    kind: str


class ProjectedDayOut(BaseModel):
    date: date
    balance_minor: int
    events: list[ProjectedEventOut]


class ProjectionOut(BaseModel):
    available: bool = Field(description="False when no account balance is known")
    reason: str | None = None
    start_balance_minor: int | None = None
    daily_variable_spend_minor: int = Field(
        default=0, description="Average daily spending that is not a recurring bill (last 90 days)"
    )
    lowest_balance_minor: int | None = None
    lowest_on: date | None = None
    goes_negative_on: date | None = None
    days: list[ProjectedDayOut] = []
    assumptions: list[str] = []


class CashflowOut(BaseModel):
    currency: str
    months: list[MonthCashflowOut]
    projection: ProjectionOut


class CategoryMonthOut(BaseModel):
    month: date
    total_minor: int


class CategoryTrendOut(BaseModel):
    category_id: str | None
    name: str
    slug: str | None
    total_minor: int
    series: list[CategoryMonthOut]
    change_pct: float | None = Field(
        description="Last complete month vs the month before it; null if no baseline"
    )


class MerchantTotalOut(BaseModel):
    merchant: str
    total_minor: int
    count: int
    last_on: date


class SpendTrendsOut(BaseModel):
    currency: str
    months: list[date]
    total_by_month: list[CategoryMonthOut]
    categories: list[CategoryTrendOut]
    top_merchants: list[MerchantTotalOut]


class CardDueOut(BaseModel):
    account_id: str
    name: str
    last4: str | None
    source: str = Field(description="statement | account_settings | none")
    statement_date: date | None
    due_date: date | None
    days_left: int | None = Field(description="Negative when overdue")
    minimum_due_minor: int | None
    total_due_minor: int | None
    remaining_minimum_due_minor: int | None = Field(
        description="Minimum due left after payments since the statement"
    )
    remaining_total_due_minor: int | None
    paid_since_statement_minor: int
    status: str = Field(description="overdue | due_soon | upcoming | paid | unknown")
    credit_limit_minor: int | None
    owed_now_minor: int | None
    utilisation_pct: float | None
    available_credit_minor: int | None
    nudges: list[dict[str, str]]


class DueDatesOut(BaseModel):
    as_of: date
    cards: list[CardDueOut]


class NetWorthPointOut(BaseModel):
    date: date
    assets_minor: int
    liabilities_minor: int
    net_worth_minor: int


class NetWorthHistoryOut(BaseModel):
    currency: str
    points: list[NetWorthPointOut]
    change_minor: int | None = Field(description="Latest minus earliest point in the window")
    note: str


class CommitmentOut(BaseModel):
    kind: Literal["bill", "loan", "card_due", "goal"]
    label: str
    amount_minor: int
    due_on: date | None


class SafeToSpendOut(BaseModel):
    currency: str
    as_of: date
    until: date = Field(description="Next expected salary date, else the end of the month")
    until_basis: str = Field(description="salary | month_end")
    days_left: int
    status: Literal["ok", "shortfall", "unknown"]
    liquid_minor: int | None = Field(description="Known balances of bank, cash and wallet accounts")
    committed_minor: int
    safe_to_spend_minor: int | None = Field(description="liquid minus commitments; negative = shortfall")
    per_day_minor: int | None
    by_kind: dict[str, int]
    breakdown: list[CommitmentOut]
    notes: list[str]


class HealthComponentOut(BaseModel):
    key: str
    weight: int
    score: int | None
    value: float | None
    detail: str


class HealthScoreOut(BaseModel):
    score: int | None
    band: str | None
    coverage: float = Field(description="Share of the score's weight that had data")
    summary: str
    components: list[HealthComponentOut]

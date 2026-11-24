from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SalaryRecordOut(ORMModel):
    id: UUID
    account_id: UUID | None
    transaction_id: UUID | None
    employer: str | None
    amount_minor: int
    currency: str
    received_on: date
    matched_by: str | None = Field(description="Marker that fired: wps | payroll | salary")


class MonthlyTotal(BaseModel):
    month: date = Field(description="First day of the month")
    amount_minor: int


class EmployerOut(BaseModel):
    employer: str | None
    payments: int
    latest_amount_minor: int


class SalarySummary(BaseModel):
    detected: bool = Field(description="False until a salary credit has been recognised")
    currency: str | None
    is_recurring: bool = Field(description="Salary seen in two or more distinct months")
    months_observed: int
    latest: SalaryRecordOut | None
    latest_month_minor: int | None
    previous_month_minor: int | None
    change_minor: int | None = Field(description="latest month minus the previous month")
    average_monthly_minor: int | None = Field(description="Mean of the last three months with a salary")
    typical_day: int | None = Field(description="Usual day of the month the salary lands")
    next_expected_on: date | None
    monthly: list[MonthlyTotal]
    employers: list[EmployerOut]
    records: list[SalaryRecordOut]


class DetectOut(BaseModel):
    scanned: int = Field(description="Credits examined")
    detected: int = Field(description="New salary records created")

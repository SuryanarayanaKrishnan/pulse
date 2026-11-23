from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.household import HouseholdRole, MemberStatus, ShareLevel


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(
        default=None, max_length=120, description="How you appear to the household"
    )


class HouseholdUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class InviteIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["adult", "dependent"]
    phone: str | None = Field(default=None, max_length=32, description="UAE mobile in any common format")
    email: EmailStr | None = None

    @model_validator(mode="after")
    def _one_contact(self) -> "InviteIn":
        if bool(self.phone) == bool(self.email):
            raise ValueError("Provide exactly one of phone or email")
        return self


class MemberOut(BaseModel):
    id: UUID
    display_name: str
    role: HouseholdRole
    status: MemberStatus
    is_you: bool
    share_level: ShareLevel = Field(description="What this member shares with the household")
    invited_via: str | None = Field(default=None, description="Masked phone or email an invite was sent to")
    joined_at: datetime | None


class HouseholdOut(BaseModel):
    id: UUID
    name: str
    your_role: HouseholdRole
    your_member_id: UUID
    members: list[MemberOut]


class HouseholdSummaryOut(BaseModel):
    id: UUID
    name: str
    your_role: HouseholdRole
    member_count: int


class InvitationOut(BaseModel):
    household_id: UUID
    household_name: str
    member_id: UUID
    role: HouseholdRole
    invited_by_name: str | None


class SharingIn(BaseModel):
    share_level: ShareLevel


class RoleIn(BaseModel):
    role: Literal["adult", "dependent"]


class CategoryAmountOut(BaseModel):
    name: str
    slug: str | None
    total_minor: int


class MemberDataOut(BaseModel):
    income_minor: int
    spend_minor: int
    net_minor: int
    net_worth_minor: int
    categories: list[CategoryAmountOut] | None = Field(description="Only at categories or full")
    transactions_available: bool = Field(description="True at full: see .../members/{id}/transactions")


class MemberOverviewOut(BaseModel):
    member_id: UUID
    display_name: str
    role: HouseholdRole
    is_you: bool
    shares: ShareLevel = Field(description="What this member chose to share")
    you_can_see: ShareLevel = Field(description="What you actually see (a dependent sees only their own)")
    data: MemberDataOut | None


class HouseholdTotalsOut(BaseModel):
    members_included: int = Field(description="Members whose totals are in these numbers")
    members_not_shared: int
    income_minor: int
    spend_minor: int
    net_minor: int
    net_worth_minor: int
    category_members_included: int
    categories: list[CategoryAmountOut]


class OverviewOut(BaseModel):
    household_id: UUID
    name: str
    month: date
    currency: str
    members: list[MemberOverviewOut]
    household: HouseholdTotalsOut
    notes: list[str]


class MemberTxnOut(BaseModel):
    id: UUID
    posted_at: datetime
    amount_minor: int
    direction: str
    merchant: str | None
    category: str | None

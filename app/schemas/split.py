from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.split import SettlementMethod, SettlementStatus, SplitType
from app.schemas.common import Currency, ORMModel, PositiveMinor, Timestamps


class MemberIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: EmailStr | None = None
    user_id: UUID | None = None
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="Mobile; UAE shorthand (05x...) is normalised to E.164. If it belongs to a Pulse user "
        "who is discoverable, the member is linked to that user.",
    )


class MemberOut(ORMModel):
    id: UUID
    user_id: UUID | None
    display_name: str
    email: str | None
    phone: str | None = Field(default=None, validation_alias="phone_e164")


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency: Currency = "AED"
    members: list[MemberIn] = Field(default_factory=list, max_length=50)


class GroupOut(Timestamps):
    name: str
    currency: str
    created_by: UUID
    members: list[MemberOut] = []


class Participant(BaseModel):
    member_id: UUID
    amount_minor: int | None = Field(default=None, ge=0)  # exact
    percent: Decimal | None = Field(default=None, ge=0, le=100)  # percent


class ExpenseCreate(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    amount_minor: PositiveMinor
    currency: Currency | None = None
    paid_by_member_id: UUID
    split_type: SplitType = SplitType.equal
    participants: list[Participant] = Field(min_length=1, max_length=50)
    spent_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_participant_fields(self) -> "ExpenseCreate":
        if self.split_type is SplitType.exact and any(p.amount_minor is None for p in self.participants):
            raise ValueError("amount_minor is required for every participant in an exact split")
        if self.split_type is SplitType.percent and any(p.percent is None for p in self.participants):
            raise ValueError("percent is required for every participant in a percent split")
        return self


class ShareOut(ORMModel):
    member_id: UUID
    share_minor: int
    percent: Decimal | None


class ExpenseOut(Timestamps):
    group_id: UUID
    paid_by_member_id: UUID
    description: str
    amount_minor: int
    currency: str
    split_type: SplitType
    spent_at: datetime
    notes: str | None
    shares: list[ShareOut] = []


class SettlementCreate(BaseModel):
    from_member_id: UUID
    to_member_id: UUID
    amount_minor: PositiveMinor
    settled_at: datetime | None = None
    note: str | None = Field(default=None, max_length=200)
    method: SettlementMethod = Field(
        default=SettlementMethod.other,
        description="aani starts pending until the payee confirms; other methods are confirmed immediately",
    )

    @model_validator(mode="after")
    def _distinct(self) -> "SettlementCreate":
        if self.from_member_id == self.to_member_id:
            raise ValueError("from_member_id and to_member_id must differ")
        return self


class SettlementOut(Timestamps):
    group_id: UUID
    from_member_id: UUID
    to_member_id: UUID
    amount_minor: int
    currency: str
    settled_at: datetime
    note: str | None
    method: SettlementMethod
    status: SettlementStatus
    marked_paid_at: datetime | None
    confirmed_at: datetime | None
    can_mark_paid: bool = False
    can_confirm: bool = False
    can_cancel: bool = False


class AaniRequestIn(BaseModel):
    to_member_id: UUID = Field(description="Who is being paid")
    from_member_id: UUID | None = Field(default=None, description="Payer; defaults to you")
    amount_minor: PositiveMinor
    note: str | None = Field(default=None, max_length=200)


class AaniRequestOut(BaseModel):
    settlement: SettlementOut
    amount_minor: int
    currency: str
    amount_display: str
    payee_display_name: str
    payee_phone: str = Field(description="E.164 mobile to pay in the bank app's Aani 'pay by mobile number'")
    share_text: str
    instructions: list[str]


class BalanceOut(BaseModel):
    member_id: UUID
    display_name: str
    net_minor: int  # positive: is owed; negative: owes


class TransferOut(BaseModel):
    from_member_id: UUID
    to_member_id: UUID
    amount_minor: int


class GroupBalancesOut(BaseModel):
    currency: str
    balances: list[BalanceOut]
    transfers: list[TransferOut]

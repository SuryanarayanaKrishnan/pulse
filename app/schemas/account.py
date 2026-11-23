from pydantic import BaseModel, Field

from app.models.account import AccountType
from app.schemas.common import Currency, MinorAmount, Timestamps


class AccountBase(BaseModel):
    type: AccountType
    name: str = Field(min_length=1, max_length=120)
    institution: str | None = Field(default=None, max_length=80)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    currency: Currency = "AED"
    credit_limit_minor: MinorAmount | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)


class AccountCreate(AccountBase):
    pass


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    institution: str | None = Field(default=None, max_length=80)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    credit_limit_minor: MinorAmount | None = None
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)


class AccountOut(AccountBase, Timestamps):
    pass

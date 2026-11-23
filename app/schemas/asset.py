from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.asset import AssetKind, LiabilityKind, ValuationSource
from app.schemas.common import Currency, MinorAmount, Timestamps


class AssetBase(BaseModel):
    kind: AssetKind
    name: str = Field(min_length=1, max_length=160)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=8)
    unit: str | None = Field(default=None, max_length=20)
    currency: Currency = "AED"
    cost_basis_minor: MinorAmount | None = None
    manual_value_minor: MinorAmount | None = None
    valuation_source: ValuationSource = ValuationSource.manual
    acquired_on: date | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class AssetCreate(AssetBase):
    pass


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    quantity: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=8)
    unit: str | None = Field(default=None, max_length=20)
    cost_basis_minor: MinorAmount | None = None
    manual_value_minor: MinorAmount | None = None
    valuation_source: ValuationSource | None = None
    meta: dict[str, Any] | None = None


class AssetOut(AssetBase, Timestamps):
    valued_at: datetime | None = None


class LiabilityBase(BaseModel):
    kind: LiabilityKind
    name: str = Field(min_length=1, max_length=160)
    lender: str | None = Field(default=None, max_length=120)
    currency: Currency = "AED"
    principal_minor: MinorAmount | None = None
    outstanding_minor: MinorAmount
    interest_rate_bps: int | None = Field(default=None, ge=0, le=100_000)
    emi_minor: MinorAmount | None = None
    next_due_on: date | None = None
    end_date: date | None = None
    account_id: UUID | None = None


class LiabilityCreate(LiabilityBase):
    pass


class LiabilityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    lender: str | None = Field(default=None, max_length=120)
    outstanding_minor: MinorAmount | None = None
    interest_rate_bps: int | None = Field(default=None, ge=0, le=100_000)
    emi_minor: MinorAmount | None = None
    next_due_on: date | None = None
    end_date: date | None = None


class LiabilityOut(LiabilityBase, Timestamps):
    pass


class NetWorthOut(BaseModel):
    base_currency: str
    total_assets_minor: int
    total_liabilities_minor: int
    net_worth_minor: int
    assets_by_kind: dict[str, int]
    liabilities_by_kind: dict[str, int]
    unconverted_currencies: list[str]

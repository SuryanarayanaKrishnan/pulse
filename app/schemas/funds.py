from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.portfolio import FundKind, FundTxnKind
from app.schemas.common import Currency, MinorAmount, Timestamps


class FundSchemeOut(BaseModel):
    scheme_code: str
    name: str


class FundNavOut(BaseModel):
    scheme_code: str
    name: str | None
    nav_date: date
    nav: Decimal


class HoldingCreate(BaseModel):
    kind: FundKind
    name: str = Field(min_length=1, max_length=240)
    scheme_code: str | None = Field(
        default=None, max_length=40, description="AMFI code (mfapi.in) for Indian MFs"
    )
    isin: str | None = Field(default=None, max_length=20)
    folio: str | None = Field(default=None, max_length=40)
    currency: Currency | None = Field(default=None, description="Default INR for india_mf, AED for manual")
    units: Decimal = Field(default=Decimal(0), ge=0, max_digits=24, decimal_places=6)
    avg_cost_per_unit: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    sip_amount_minor: MinorAmount | None = None
    sip_day: int | None = Field(default=None, ge=1, le=31)
    manual_nav: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)


class HoldingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    scheme_code: str | None = Field(default=None, max_length=40)
    units: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=6)
    avg_cost_per_unit: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    sip_amount_minor: MinorAmount | None = None
    sip_day: int | None = Field(default=None, ge=1, le=31)


class NavIn(BaseModel):
    nav: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    as_of: date | None = None


class NavInfo(BaseModel):
    value: Decimal | None
    as_of: date | None
    basis: str = Field(description="market | manual | stale_market | none")


class HoldingOut(Timestamps):
    kind: FundKind
    name: str
    scheme_code: str | None
    isin: str | None
    folio: str | None
    currency: str
    units: Decimal
    avg_cost_per_unit: Decimal | None
    sip_amount_minor: int | None
    sip_day: int | None
    nav: NavInfo
    value_minor: int | None = Field(description="units x NAV in the fund's own currency")
    value_aed_minor: int | None = Field(description="Converted to AED; null when no FX rate is known")
    fx_rate_to_aed: Decimal | None
    cost_minor: int | None
    gain_minor: int | None
    gain_pct: float | None
    last_updated: datetime | None
    note: str | None


class FundsOut(BaseModel):
    holdings: list[HoldingOut]
    total_value_aed_minor: int
    total_cost_aed_minor: int | None
    unconverted: list[str]


class FundTxnIn(BaseModel):
    kind: FundTxnKind
    txn_date: date
    units: Decimal = Field(max_digits=24, decimal_places=6, description="Positive; the sign follows the kind")
    nav: Decimal | None = Field(default=None, gt=0)
    amount_minor: MinorAmount | None = Field(default=None, description="Defaults to units x nav")
    description: str | None = Field(default=None, max_length=300)


class FundTxnOut(BaseModel):
    id: UUID
    txn_date: date
    kind: FundTxnKind
    units: Decimal
    nav: Decimal | None
    amount_minor: int
    description: str | None
    source: str


class CasImportOut(BaseModel):
    holdings_created: int
    holdings_updated: int
    transactions_added: int
    transactions_skipped_duplicate: int
    unresolved_schemes: list[str] = Field(description="No AMFI code found: set scheme_code to get live NAVs")
    warnings: list[str]
    holdings: list[HoldingOut]

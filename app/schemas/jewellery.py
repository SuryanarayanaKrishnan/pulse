from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import MinorAmount


class JewelleryBase(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    weight_grams: Decimal = Field(gt=0, le=Decimal(100000), max_digits=12, decimal_places=3)
    karat: int | None = Field(default=None, ge=8, le=24, description="e.g. 22; or give purity")
    purity: Decimal | None = Field(default=None, ge=Decimal("0.3"), le=1, description="Fineness, e.g. 0.916")
    making_charges_minor: MinorAmount | None = None
    purchase_price_minor: MinorAmount | None = Field(
        default=None, description="Total paid incl. making charges"
    )
    purchase_date: date | None = None
    photo_keys: list[str] = Field(
        default_factory=list, max_length=20, description="Opaque keys; photos stay on-device"
    )


class JewelleryCreate(JewelleryBase):
    @model_validator(mode="after")
    def _purity(self) -> "JewelleryCreate":
        if self.karat is None and self.purity is None:
            raise ValueError("Provide karat or purity")
        return self


class JewelleryUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=240)
    weight_grams: Decimal | None = Field(
        default=None, gt=0, le=Decimal(100000), max_digits=12, decimal_places=3
    )
    karat: int | None = Field(default=None, ge=8, le=24)
    purity: Decimal | None = Field(default=None, ge=Decimal("0.3"), le=1)
    making_charges_minor: MinorAmount | None = None
    purchase_price_minor: MinorAmount | None = None
    purchase_date: date | None = None
    photo_keys: list[str] | None = Field(default=None, max_length=20)


class BackupBlobIn(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    encrypted: bool = Field(description="Must be true: the server only tracks encrypted backups")


class JewelleryValuationOut(BaseModel):
    market_value_minor: int | None = Field(description="Melt (spot) value: weight x purity x live gold price")
    basis: str
    per_gram_aed_minor: int | None
    last_updated: datetime | None
    note: str | None
    purchase_price_minor: int | None
    gain_loss_minor: int | None = Field(description="Market value minus purchase price")
    gain_loss_pct: float | None
    explanation: str


class JewelleryOut(BaseModel):
    asset_id: UUID
    description: str
    weight_grams: Decimal
    karat: int | None
    purity: Decimal | None
    fineness: Decimal = Field(description="The purity used for valuation")
    making_charges_minor: int | None
    purchase_price_minor: int | None
    purchase_date: date | None
    photo_keys: list[str]
    backup_blob: dict[str, Any] | None
    valuation: JewelleryValuationOut


class ValuationPointOut(BaseModel):
    date: date
    value_minor: int
    basis: str


class ValuationHistoryOut(BaseModel):
    asset_id: UUID
    points: list[ValuationPointOut]
    change_minor: int | None

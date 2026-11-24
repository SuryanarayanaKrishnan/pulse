from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class MoneyPair(BaseModel):
    aed: Decimal
    usd: Decimal


class MetalOut(BaseModel):
    status: str = Field(description="live (inside the TTL window) | stale | unavailable")
    per_oz: MoneyPair | None = Field(description="Per troy ounce of pure metal")
    per_gram: dict[str, MoneyPair] = Field(description="Gold by karat 24k/22k/21k/18k; silver as 'pure'")
    last_updated: datetime | None
    source: str | None


class FxOut(BaseModel):
    currency: str
    aed_per_unit: Decimal
    status: str
    last_updated: datetime | None
    source: str | None


class SymbolPriceOut(BaseModel):
    symbol: str
    usd: Decimal
    aed: Decimal
    status: str
    last_updated: datetime
    source: str


class PriceSnapshotOut(BaseModel):
    generated_at: datetime
    ttl_seconds: int = Field(description="How long the phone may cache this payload")
    status: str = Field(description="live | stale | unavailable (gold is the reference)")
    gold: MetalOut
    silver: MetalOut
    fx: list[FxOut]
    crypto: list[SymbolPriceOut]
    stocks: list[SymbolPriceOut]
    notes: list[str]


class HistoryPoint(BaseModel):
    date: date
    value: Decimal


class HistoryOut(BaseModel):
    symbol: str
    unit: str
    source: str | None
    points: list[HistoryPoint]
    note: str | None

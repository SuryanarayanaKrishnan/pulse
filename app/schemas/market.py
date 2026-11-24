from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class GoldQuoteOut(BaseModel):
    status: str = Field(description="live (under 24h) | stale | unavailable")
    usd_per_oz: Decimal | None
    aed_per_gram: dict[str, Decimal] = Field(description="Spot value per gram by purity (24k, 22k, 21k, 18k)")
    last_updated: datetime | None
    source: str | None
    note: str = "Spot price: retail and jewellery prices add making charges and a dealer spread"


class FxQuoteOut(BaseModel):
    currency: str
    aed_per_unit: Decimal
    status: str = Field(description="live | stale | peg (fixed AED/USD rate, no data needed)")
    last_updated: datetime | None
    source: str | None


class IndexQuoteOut(BaseModel):
    key: str
    latest_value: Decimal
    index_date: date
    last_updated: datetime


class MarketQuotesOut(BaseModel):
    gold: GoldQuoteOut
    fx: list[FxQuoteOut]
    property_indices: list[IndexQuoteOut]

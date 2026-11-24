import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import pk_uuid


class MarketQuote(Base):
    """Cached market data (not per-user): one row per symbol per day, updated in place.

    Symbols: XAU_USD_OZ (USD per troy ounce of gold), FX_<CCY>_AED (AED per 1 unit of CCY),
    IDX_<key> (a property-index level; quote_date is the index date).
    """

    __tablename__ = "market_quotes"
    __table_args__ = (
        UniqueConstraint("symbol", "quote_date", name="uq_market_quotes_symbol_date"),
        Index("ix_market_quotes_symbol", "symbol"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    symbol: Mapped[str] = mapped_column(String(60))
    quote_date: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    source: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

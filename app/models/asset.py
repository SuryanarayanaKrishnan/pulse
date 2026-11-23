import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import (
    JSONDict,
    JSONType,
    SoftDeleteMixin,
    TimestampMixin,
    currency_col,
    pk_uuid,
    str_enum,
)


class AssetKind(enum.StrEnum):
    real_estate = "real_estate"
    fd = "fd"
    gold = "gold"
    stock = "stock"
    crypto = "crypto"
    cash = "cash"
    vehicle = "vehicle"
    other = "other"


class ValuationSource(enum.StrEnum):
    manual = "manual"
    market = "market"
    estimate = "estimate"


class LiabilityKind(enum.StrEnum):
    credit_card = "credit_card"
    personal_loan = "personal_loan"
    auto_loan = "auto_loan"
    mortgage = "mortgage"
    other = "other"


class Asset(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[AssetKind] = mapped_column(str_enum(AssetKind, "asset_kind"))
    name: Mapped[str] = mapped_column(String(160))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    unit: Mapped[str | None] = mapped_column(String(20))  # g, oz, shares, sqft, coin...
    currency: Mapped[str] = currency_col()
    cost_basis_minor: Mapped[int | None] = mapped_column(BigInteger)
    manual_value_minor: Mapped[int | None] = mapped_column(BigInteger)
    valuation_source: Mapped[ValuationSource] = mapped_column(
        str_enum(ValuationSource, "valuation_source"), server_default="manual"
    )
    valued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquired_on: Mapped[date | None] = mapped_column(Date)
    # kind-specific data: {"ticker": "EMAAR"}, {"purity": "22k"}, {"maturity_date": ...}
    meta: Mapped[JSONDict] = mapped_column("metadata", JSONType, server_default="{}")


class Liability(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "liabilities"
    __table_args__ = (Index("ix_liabilities_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    kind: Mapped[LiabilityKind] = mapped_column(str_enum(LiabilityKind, "liability_kind"))
    name: Mapped[str] = mapped_column(String(160))
    lender: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = currency_col()
    principal_minor: Mapped[int | None] = mapped_column(BigInteger)
    outstanding_minor: Mapped[int] = mapped_column(BigInteger)
    interest_rate_bps: Mapped[int | None] = mapped_column()
    emi_minor: Mapped[int | None] = mapped_column(BigInteger)
    next_due_on: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

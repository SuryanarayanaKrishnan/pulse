import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
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


class FundKind(enum.StrEnum):
    india_mf = "india_mf"  # priced from mfapi.in (INR NAV)
    manual = "manual"  # UAE/other funds: the user enters the NAV


class FundTxnKind(enum.StrEnum):
    purchase = "purchase"
    sip = "sip"
    redemption = "redemption"
    switch_in = "switch_in"
    switch_out = "switch_out"
    dividend = "dividend"
    other = "other"


class FundHolding(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "fund_holdings"
    __table_args__ = (Index("ix_fund_holdings_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[FundKind] = mapped_column(str_enum(FundKind, "fund_kind"))
    name: Mapped[str] = mapped_column(String(240))
    scheme_code: Mapped[str | None] = mapped_column(String(40))  # AMFI code for mfapi.in
    isin: Mapped[str | None] = mapped_column(String(20))
    folio: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str] = currency_col()  # currency of NAV and cost (INR for Indian MFs)
    units: Mapped[Decimal] = mapped_column(Numeric(24, 6), server_default="0")
    avg_cost_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    sip_amount_minor: Mapped[int | None] = mapped_column(BigInteger)  # in `currency`
    sip_day: Mapped[int | None] = mapped_column(SmallInteger)
    manual_nav: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    manual_nav_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FundTransaction(Base, TimestampMixin):
    __tablename__ = "fund_transactions"
    __table_args__ = (
        UniqueConstraint("holding_id", "dedupe_hash", name="uq_fund_transactions_holding_hash"),
        Index("ix_fund_transactions_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    holding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fund_holdings.id", ondelete="CASCADE")
    )
    txn_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[FundTxnKind] = mapped_column(str_enum(FundTxnKind, "fund_txn_kind"))
    units: Mapped[Decimal] = mapped_column(Numeric(24, 6))  # negative when units leave
    nav: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    amount_minor: Mapped[int] = mapped_column(BigInteger)  # signed, in the holding's currency
    description: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(10), server_default="manual")  # manual | cas
    dedupe_hash: Mapped[str] = mapped_column(String(64))


class AssetValuation(Base):
    """One automatic valuation per asset per day (history for charts and gain/loss)."""

    __tablename__ = "asset_valuations"
    __table_args__ = (UniqueConstraint("asset_id", "valued_on", name="uq_asset_valuations_asset_day"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE")
    )
    valued_on: Mapped[date] = mapped_column(Date)
    value_minor: Mapped[int] = mapped_column(BigInteger)
    basis: Mapped[str] = mapped_column(String(12))


class JewelleryItem(Base, TimestampMixin):
    __tablename__ = "jewellery_items"
    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_jewellery_items_asset_id"),
        Index("ix_jewellery_items_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE")
    )
    description: Mapped[str] = mapped_column(String(240))
    weight_grams: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    karat: Mapped[int | None] = mapped_column(SmallInteger)
    purity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))  # fineness override, e.g. 0.9160
    making_charges_minor: Mapped[int | None] = mapped_column(BigInteger)
    purchase_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    # Photos live on the device; only opaque storage keys are kept here.
    photo_keys: Mapped[list[str]] = mapped_column(ARRAY(String(512)), server_default="{}")
    # Optional encrypted backup blob metadata: {"key", "size", "sha256", "encrypted": true, "updated_at"}
    backup_blob: Mapped[JSONDict | None] = mapped_column(JSONType)


class Document(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(
        String(24)
    )  # title_deed | fd_certificate | insurance | passport | other...
    title: Mapped[str] = mapped_column(String(200))
    institution: Mapped[str | None] = mapped_column(String(160))
    reference: Mapped[str | None] = mapped_column(String(120))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = currency_col()
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(512))  # optional encrypted backup, metadata only
    blob_size: Mapped[int | None] = mapped_column(BigInteger)
    blob_sha256: Mapped[str | None] = mapped_column(String(64))
    blob_encrypted: Mapped[bool] = mapped_column(Boolean, server_default="true")


class DocumentReminder(Base, TimestampMixin):
    __tablename__ = "document_reminders"
    __table_args__ = (Index("ix_document_reminders_user_due", "user_id", "due_on"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(12))  # maturity | renewal | expiry | custom
    label: Mapped[str | None] = mapped_column(String(160))
    due_on: Mapped[date] = mapped_column(Date)
    remind_days_before: Mapped[int] = mapped_column(SmallInteger, server_default="30")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

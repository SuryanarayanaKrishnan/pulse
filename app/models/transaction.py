import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
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
from app.models.statement import ParseStatus


class Direction(enum.StrEnum):
    debit = "debit"
    credit = "credit"


class TxnSource(enum.StrEnum):
    statement = "statement"
    sms = "sms"
    notification = "notification"
    apple_shortcut = "apple_shortcut"
    manual = "manual"


class Transaction(Base, TimestampMixin, SoftDeleteMixin):
    """amount_minor is always positive; `direction` carries the sign."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="amount_non_negative"),
        UniqueConstraint("user_id", "external_hash", name="uq_transactions_user_id_external_hash"),
        Index("ix_transactions_user_id_posted_at", "user_id", "posted_at"),
        Index("ix_transactions_account_id_posted_at", "account_id", "posted_at"),
        Index("ix_transactions_statement_id", "statement_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE")
    )
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statements.id", ondelete="SET NULL")
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = currency_col()
    direction: Mapped[Direction] = mapped_column(str_enum(Direction, "direction"))
    merchant_raw: Mapped[str | None] = mapped_column(Text)
    merchant_normalized: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[TxnSource] = mapped_column(str_enum(TxnSource, "txn_source"))
    external_hash: Mapped[str | None] = mapped_column(String(64))
    # Set by classification rules (or by hand): kept out of budget/spend totals, free-form labels.
    exclude_from_budget: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default=text("'{}'::varchar[]"))
    applied_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchant_rules.id", ondelete="SET NULL")
    )


class RawIngest(Base, TimestampMixin):
    """Raw SMS / notification / shortcut payloads, kept verbatim for reprocessing."""

    __tablename__ = "raw_ingests"
    __table_args__ = (
        UniqueConstraint("user_id", "external_hash", name="uq_raw_ingests_user_id_external_hash"),
        Index("ix_raw_ingests_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[TxnSource] = mapped_column(str_enum(TxnSource, "ingest_channel"))
    external_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[JSONDict] = mapped_column(JSONType)
    parse_status: Mapped[ParseStatus] = mapped_column(
        str_enum(ParseStatus, "ingest_parse_status"), server_default="pending"
    )
    parse_result: Mapped[JSONDict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL")
    )

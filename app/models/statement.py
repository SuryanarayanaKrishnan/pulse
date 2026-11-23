import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, currency_col, pk_uuid, str_enum


class ParseStatus(enum.StrEnum):
    pending = "pending"
    parsed = "parsed"
    failed = "failed"
    needs_review = "needs_review"
    skipped = "skipped"


class ReconciliationStatus(enum.StrEnum):
    pending = "pending"
    matched = "matched"
    mismatch = "mismatch"


class Statement(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "statements"
    __table_args__ = (Index("ix_statements_user_id_period_end", "user_id", "period_end"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE")
    )
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = currency_col()
    opening_balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    closing_balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    file_storage_key: Mapped[str | None] = mapped_column(String(512))
    parse_status: Mapped[ParseStatus] = mapped_column(
        str_enum(ParseStatus, "parse_status"), server_default="pending"
    )
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        str_enum(ReconciliationStatus, "reconciliation_status"), server_default="pending"
    )
    # statement closing - (opening + parsed transactions); 0 when reconciled
    diff_minor: Mapped[int | None] = mapped_column(BigInteger)

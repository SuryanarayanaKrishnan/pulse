import uuid
from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import JSONDict, JSONType, SoftDeleteMixin, TimestampMixin, currency_col, pk_uuid


class SavingsGoal(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "savings_goals"
    __table_args__ = (
        CheckConstraint("target_minor > 0", name="goal_target_positive"),
        CheckConstraint("saved_minor >= 0", name="goal_saved_non_negative"),
        Index("ix_savings_goals_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    target_minor: Mapped[int] = mapped_column(BigInteger)
    saved_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    currency: Mapped[str] = currency_col()
    target_date: Mapped[date | None] = mapped_column(Date)
    # What the user plans to put aside each month; when null it is derived from target and date.
    monthly_contribution_minor: Mapped[int | None] = mapped_column(BigInteger)


class NetWorthSnapshot(Base, TimestampMixin):
    __tablename__ = "net_worth_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "snapshot_date", name="uq_net_worth_snapshots_user_date"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = currency_col()
    assets_minor: Mapped[int] = mapped_column(BigInteger)
    liabilities_minor: Mapped[int] = mapped_column(BigInteger)
    net_worth_minor: Mapped[int] = mapped_column(BigInteger)
    breakdown: Mapped[JSONDict] = mapped_column(JSONType, server_default="{}")


class RecurringReview(Base, TimestampMixin):
    """The user's verdict on a detected recurring series: keep it (confirmed) or hide it (dismissed)."""

    __tablename__ = "recurring_reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "series_key", name="uq_recurring_reviews_user_key"),
        CheckConstraint("status IN ('confirmed', 'dismissed')", name="recurring_review_status"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    series_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(12))

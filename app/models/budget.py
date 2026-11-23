import uuid
from datetime import date

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, currency_col, pk_uuid


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", "month", name="uq_budgets_user_category_month"),
        CheckConstraint("limit_minor >= 0", name="limit_non_negative"),
        CheckConstraint("EXTRACT(DAY FROM month) = 1", name="month_is_first_day"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE")
    )
    month: Mapped[date] = mapped_column(Date)  # first day of the month
    limit_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = currency_col()


class SalaryRecord(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "salary_records"
    __table_args__ = (Index("ix_salary_records_user_id_received_on", "user_id", "received_on"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    employer: Mapped[str | None] = mapped_column(String(160))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = currency_col()
    received_on: Mapped[date] = mapped_column(Date)

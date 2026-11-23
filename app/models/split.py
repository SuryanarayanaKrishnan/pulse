import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, currency_col, pk_uuid, str_enum


class SplitType(enum.StrEnum):
    equal = "equal"
    exact = "exact"
    percent = "percent"


class SplitGroup(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "split_groups"

    id: Mapped[uuid.UUID] = pk_uuid()
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = currency_col()


class SplitMember(Base, TimestampMixin):
    """A person in a group. user_id is NULL for friends who are not on the app."""

    __tablename__ = "split_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_split_members_group_id_user_id"),
        Index("ix_split_members_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_groups.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320))


class SplitExpense(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "split_expenses"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index("ix_split_expenses_group_id_spent_at", "group_id", "spent_at"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_groups.id", ondelete="CASCADE")
    )
    paid_by_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_members.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    description: Mapped[str] = mapped_column(String(200))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = currency_col()
    split_type: Mapped[SplitType] = mapped_column(str_enum(SplitType, "split_type"))
    spent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class SplitShare(Base):
    __tablename__ = "split_shares"
    __table_args__ = (
        UniqueConstraint("expense_id", "member_id", name="uq_split_shares_expense_id_member_id"),
        CheckConstraint("share_minor >= 0", name="share_non_negative"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_expenses.id", ondelete="CASCADE")
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_members.id", ondelete="RESTRICT")
    )
    share_minor: Mapped[int] = mapped_column(BigInteger)
    percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))


class Settlement(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("from_member_id <> to_member_id", name="distinct_members"),
        Index("ix_settlements_group_id", "group_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_groups.id", ondelete="CASCADE")
    )
    from_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_members.id", ondelete="RESTRICT")
    )
    to_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("split_members.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = currency_col()
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(200))

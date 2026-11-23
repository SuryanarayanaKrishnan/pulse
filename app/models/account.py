import enum
import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, currency_col, pk_uuid, str_enum


class AccountType(enum.StrEnum):
    bank = "bank"
    credit_card = "credit_card"
    wallet = "wallet"
    cash = "cash"


class Account(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("statement_day BETWEEN 1 AND 31", name="statement_day_range"),
        CheckConstraint("due_day BETWEEN 1 AND 31", name="due_day_range"),
        Index("ix_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[AccountType] = mapped_column(str_enum(AccountType, "account_type"))
    name: Mapped[str] = mapped_column(String(120))
    institution: Mapped[str | None] = mapped_column(String(80))
    last4: Mapped[str | None] = mapped_column(String(4))
    currency: Mapped[str] = currency_col()
    credit_limit_minor: Mapped[int | None] = mapped_column(BigInteger)
    statement_day: Mapped[int | None] = mapped_column(SmallInteger)
    due_day: Mapped[int | None] = mapped_column(SmallInteger)

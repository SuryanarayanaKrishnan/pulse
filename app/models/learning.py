import uuid
from datetime import date

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, pk_uuid


class CategoryContribution(Base, TimestampMixin):
    """Weekly aggregate of "merchant key -> category" confirmations from opted-in phones.

    Deliberately identity-free: no user, install, IP or per-request row, only summed counts per ISO week.
    """

    __tablename__ = "category_contributions"
    __table_args__ = (UniqueConstraint("week", "merchant_key", "category", name="uq_category_contrib"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    week: Mapped[date] = mapped_column(Date, index=True)
    merchant_key: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(32))
    n: Mapped[int] = mapped_column(Integer, default=0)

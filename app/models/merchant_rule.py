import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, pk_uuid


class MerchantRule(Base, TimestampMixin):
    """'Always categorise this merchant as X' - created when a user overrides a category."""

    __tablename__ = "merchant_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "merchant_key", name="uq_merchant_rules_user_id_merchant_key"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    merchant_key: Mapped[str] = mapped_column(String(160))  # lower-cased normalised merchant
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE")
    )

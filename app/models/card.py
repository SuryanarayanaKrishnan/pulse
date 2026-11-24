import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import JSONDict, JSONType, SoftDeleteMixin, TimestampMixin, pk_uuid


class CatalogCard(Base, TimestampMixin):
    """Global (not per-user) credit card product catalog used for suggestions."""

    __tablename__ = "cards_catalog"
    __table_args__ = (UniqueConstraint("issuer", "name", name="uq_cards_catalog_issuer_name"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    issuer: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    network: Mapped[str | None] = mapped_column(String(20))
    annual_fee_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    min_salary_minor: Mapped[int | None] = mapped_column(BigInteger)
    # e.g. {"base": {"rate_bps": 100}, "categories": {"dining": {"rate_bps": 500, "cap_minor": ...}}}
    reward_structure: Mapped[JSONDict] = mapped_column(JSONType, server_default="{}")
    perks: Mapped[JSONDict] = mapped_column(JSONType, server_default="{}")
    source_url: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserCard(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "user_cards"
    __table_args__ = (Index("ix_user_cards_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    catalog_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cards_catalog.id", ondelete="RESTRICT")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    nickname: Mapped[str | None] = mapped_column(String(80))

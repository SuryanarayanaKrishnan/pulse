import uuid

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, currency_col


class User(Base, TimestampMixin, SoftDeleteMixin):
    """Mirror of a Supabase auth user. id == auth.users.id (the JWT `sub`)."""

    __tablename__ = "users"
    __table_args__ = (
        # One live account per number; deactivated users release theirs.
        Index(
            "uq_users_phone_hash",
            "phone_hash",
            unique=True,
            postgresql_where=text("phone_hash IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    default_currency: Mapped[str] = currency_col()
    # Own mobile (E.164) + its hash for contact matching; the Aani-registered mobile can differ.
    phone_e164: Mapped[str | None] = mapped_column(String(16))
    phone_hash: Mapped[str | None] = mapped_column(String(64))
    aani_phone_e164: Mapped[str | None] = mapped_column(String(16))
    phone_discoverable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

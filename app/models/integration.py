import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, pk_uuid


class EmailConnection(Base, TimestampMixin, SoftDeleteMixin):
    """Mailbox link used later to pull bank statements. Tokens are Fernet-encrypted."""

    __tablename__ = "email_connections"
    __table_args__ = (Index("ix_email_connections_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32))  # gmail | outlook | imap
    email: Mapped[str] = mapped_column(String(320))
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StatementPassword(Base, TimestampMixin):
    """PDF password per institution (Fernet-encrypted), used to open emailed statements."""

    __tablename__ = "statement_passwords"
    __table_args__ = (
        UniqueConstraint("user_id", "institution", name="uq_statement_passwords_user_institution"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    institution: Mapped[str] = mapped_column(String(80))
    encrypted_password: Mapped[str] = mapped_column(Text)

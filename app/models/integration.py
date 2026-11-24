import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, pk_uuid


class EmailConnection(Base, TimestampMixin, SoftDeleteMixin):
    """Mailbox link used later to pull bank statements. Tokens are Fernet-encrypted."""

    __tablename__ = "email_connections"
    __table_args__ = (
        Index("ix_email_connections_user_id", "user_id"),
        Index(
            "uq_email_connections_user_provider_email",
            "user_id",
            "provider",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))  # gmail | outlook | imap
    email: Mapped[str] = mapped_column(String(320))
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="active")  # active | needs_reauth
    last_error: Mapped[str | None] = mapped_column(Text)


class StatementPassword(Base, TimestampMixin):
    """PDF password per institution (Fernet-encrypted), used to open emailed statements."""

    __tablename__ = "statement_passwords"
    __table_args__ = (
        UniqueConstraint("user_id", "institution", name="uq_statement_passwords_user_institution"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    institution: Mapped[str] = mapped_column(String(80))
    encrypted_password: Mapped[str] = mapped_column(Text)


class IngestToken(Base, TimestampMixin):
    """Per-user token for unattended capture (iOS Shortcuts, Google Pay / Samsung Wallet automations).

    Only the SHA-256 of the token is stored; the plaintext is shown once at creation/rotation.
    One active token per user: rotating replaces it.
    """

    __tablename__ = "ingest_tokens"
    __table_args__ = (UniqueConstraint("user_id", name="uq_ingest_tokens_user_id"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(12))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

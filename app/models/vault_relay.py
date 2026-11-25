import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, pk_uuid


class VaultRelay(Base, TimestampMixin):
    """One armed emergency file for one nominee.

    The server holds only what it cannot read: ``sealed_file`` is ciphertext (the owner's app seals it
    with a key split 2-of-3, so no share, code or key ever reaches us) and ``claim_hash`` is a SHA-256 of
    the claim code. The nominee's key shares never touch the server.
    """

    __tablename__ = "vault_relays"
    __table_args__ = (UniqueConstraint("user_id", "nominee_id", name="uq_vault_relays_user_nominee"),)

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The app's own id for the nominee; opaque to us.
    nominee_id: Mapped[str] = mapped_column(String(64))
    sealed_file: Mapped[str] = mapped_column(Text)
    claim_hash: Mapped[str] = mapped_column(String(64), unique=True)
    interval_days: Mapped[int] = mapped_column(Integer, server_default=text("90"))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set when the nominee first claims after the owner has gone quiet; the file is released only after
    # a grace period in which the owner opening the app (or pressing cancel) clears it.
    claim_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Highest warning already sent to the owner for this countdown (0 none, 1 soon, 2 claim pending).
    warned_stage: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

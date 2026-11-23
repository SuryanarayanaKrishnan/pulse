import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import JSONType, TimestampMixin, pk_uuid

AUTO_RULE_PRIORITY = 1000  # rules learnt from manual re-categorisation sit behind explicitly authored ones
DEFAULT_RULE_PRIORITY = 100


class MerchantRule(Base, TimestampMixin):
    """A user's classification rule ("automation"): AND-ed conditions -> actions.

    This table started as "always categorise merchant X as Y" (``merchant_key`` + ``category_id``). Those
    rows are now plain rules whose single condition is ``merchant equals <key>``; ``merchant_key`` stays
    as the upsert key for that learnt shortcut (NULL for rules authored through /v1/rules).
    Lower ``priority`` is evaluated first.
    """

    __tablename__ = "merchant_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "merchant_key", name="uq_merchant_rules_user_id_merchant_key"),
        Index("ix_merchant_rules_user_id_priority", "user_id", "priority"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    merchant_key: Mapped[str | None] = mapped_column(String(160))  # lower-cased normalised merchant
    name: Mapped[str | None] = mapped_column(String(120))
    priority: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    conditions: Mapped[list[dict[str, object]]] = mapped_column(JSONType, server_default=text("'[]'::jsonb"))
    # actions
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE")
    )
    rename_merchant: Mapped[str | None] = mapped_column(String(160))
    mark_transfer: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    exclude_from_budget: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), server_default=text("'{}'::varchar[]"))

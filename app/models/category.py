import enum
import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, pk_uuid, str_enum


class CategoryKind(enum.StrEnum):
    expense = "expense"
    income = "income"
    transfer = "transfer"


class Category(Base, TimestampMixin):
    """user_id NULL => system category visible to everyone."""

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_id_name"),
        Index("uq_categories_system_name", "name", unique=True, postgresql_where=text("user_id IS NULL")),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[CategoryKind] = mapped_column(
        str_enum(CategoryKind, "category_kind"), server_default="expense"
    )
    icon: Mapped[str | None] = mapped_column(String(40))

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, pk_uuid, str_enum


class CategoryKind(enum.StrEnum):
    expense = "expense"
    income = "income"
    transfer = "transfer"


class Category(Base, TimestampMixin):
    """user_id NULL => default (system) category visible to everyone; otherwise a user's custom one.

    Defaults carry a stable ``slug`` (what the merchant dictionary and the rules engine refer to), so
    they can be renamed in the UI without breaking anything. Custom categories may have one level of
    sub-categories (``parent_id`` points at a top-level default or custom category).
    """

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_id_name"),
        Index("uq_categories_system_name", "name", unique=True, postgresql_where=text("user_id IS NULL")),
        Index("uq_categories_system_slug", "slug", unique=True, postgresql_where=text("user_id IS NULL")),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    slug: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[CategoryKind] = mapped_column(
        str_enum(CategoryKind, "category_kind"), server_default="expense"
    )
    is_fixed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    icon: Mapped[str | None] = mapped_column(String(40))  # icon key; the app maps it to a glyph
    color: Mapped[str | None] = mapped_column(String(20))  # colour key; the app maps it to a palette entry
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class CategoryPreference(Base, TimestampMixin):
    """Per-user rename / hide of a category (used for the shared defaults)."""

    __tablename__ = "category_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_category_preferences_user_id_category_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE")
    )
    display_name: Mapped[str | None] = mapped_column(String(80))
    is_hidden: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

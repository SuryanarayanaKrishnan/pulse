"""Category helpers: visibility, effective (renamed) names and the one-level sub-category rule."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainValidationError
from app.models.category import Category, CategoryPreference
from app.schemas.category import CategoryOut


async def get_visible_category(session: AsyncSession, user_id: UUID, category_id: UUID) -> Category:
    """A default category or one of the user's own; anything else is reported as invalid."""
    cat = await session.get(Category, category_id)
    if cat is None or cat.user_id not in (None, user_id):
        raise DomainValidationError("Unknown category", code="invalid_category")
    return cat


async def load_preferences(session: AsyncSession, user_id: UUID) -> dict[UUID, CategoryPreference]:
    rows = await session.execute(select(CategoryPreference).where(CategoryPreference.user_id == user_id))
    return {p.category_id: p for p in rows.scalars()}


async def visible_categories(session: AsyncSession, user_id: UUID) -> list[Category]:
    rows = await session.execute(
        select(Category)
        .where(or_(Category.user_id.is_(None), Category.user_id == user_id))
        .order_by(Category.sort_order, Category.name)
    )
    return list(rows.scalars())


def category_out(cat: Category, pref: CategoryPreference | None) -> CategoryOut:
    return CategoryOut(
        id=cat.id,
        user_id=cat.user_id,
        slug=cat.slug,
        name=(pref.display_name if pref and pref.display_name else cat.name),
        default_name=cat.name,
        kind=cat.kind,
        parent_id=cat.parent_id,
        icon=cat.icon,
        color=cat.color,
        is_fixed=cat.is_fixed,
        is_default=cat.user_id is None,
        is_hidden=bool(pref and pref.is_hidden),
        sort_order=cat.sort_order,
    )


async def descendant_ids(session: AsyncSession, category_id: UUID) -> list[UUID]:
    """The category plus its direct sub-categories (the tree is one level deep)."""
    rows = await session.execute(select(Category.id).where(Category.parent_id == category_id))
    return [category_id, *rows.scalars()]

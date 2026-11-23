from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.models.category import Category, CategoryKind, CategoryPreference
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate
from app.services.categories import (
    category_out,
    get_visible_category,
    load_preferences,
    visible_categories,
)

router = APIRouter(prefix="/categories", tags=["categories"])


async def _name_taken(session: SessionDep, user_id: UUID, name: str, exclude: UUID | None = None) -> bool:
    """Names are unique per user, case-insensitively, across the defaults and the user's own."""
    stmt = select(Category.id).where(
        func.lower(Category.name) == name.strip().lower(),
        (Category.user_id.is_(None)) | (Category.user_id == user_id),
    )
    if exclude:
        stmt = stmt.where(Category.id != exclude)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    user: CurrentUser,
    session: SessionDep,
    include_hidden: Annotated[bool, Query(description="Also return categories the user has hidden")] = False,
    kind: CategoryKind | None = None,
) -> list[CategoryOut]:
    """The default set plus the user's own categories (sub-categories carry `parent_id`)."""
    prefs = await load_preferences(session, user.id)
    out = [category_out(c, prefs.get(c.id)) for c in await visible_categories(session, user.id)]
    return [c for c in out if (include_hidden or not c.is_hidden) and (kind is None or c.kind == kind)]


@router.post("", response_model=CategoryOut, status_code=201)
async def create_category(payload: CategoryCreate, user: CurrentUser, session: SessionDep) -> CategoryOut:
    kind = payload.kind
    if payload.parent_id is not None:
        parent = await get_visible_category(session, user.id, payload.parent_id)
        if parent.parent_id is not None:
            raise DomainValidationError(
                "Sub-categories can only be one level deep", code="category_depth_exceeded"
            )
        if kind is not None and kind != parent.kind:
            raise DomainValidationError("A sub-category has its parent's kind", code="invalid_kind")
        kind = parent.kind
    if await _name_taken(session, user.id, payload.name):
        raise ConflictError("A category with this name already exists", code="category_exists")
    cat = Category(
        user_id=user.id,
        parent_id=payload.parent_id,
        name=payload.name.strip(),
        kind=kind or CategoryKind.expense,
        icon=payload.icon,
        color=payload.color,
        is_fixed=payload.is_fixed,
        sort_order=1000,
    )
    session.add(cat)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A category with this name already exists", code="category_exists") from exc
    await session.refresh(cat)
    return category_out(cat, None)


async def _pref(session: SessionDep, user_id: UUID, category_id: UUID) -> CategoryPreference:
    pref = (
        await session.execute(
            select(CategoryPreference).where(
                CategoryPreference.user_id == user_id, CategoryPreference.category_id == category_id
            )
        )
    ).scalar_one_or_none()
    if pref is None:
        pref = CategoryPreference(user_id=user_id, category_id=category_id, is_hidden=False)
        session.add(pref)
    return pref


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: UUID, payload: CategoryUpdate, user: CurrentUser, session: SessionDep
) -> CategoryOut:
    cat = await session.get(Category, category_id)
    if cat is None or cat.user_id not in (None, user.id):
        raise NotFoundError("Category not found")
    data = payload.model_dump(exclude_unset=True)
    pref = await _pref(session, user.id, cat.id)
    if cat.user_id is None:
        locked = set(data) - {"name", "is_hidden"}
        if locked:
            raise DomainValidationError(
                "Default categories can only be renamed or hidden", code="default_category_locked"
            )
        if "name" in data:
            new_name = data["name"]
            if new_name is not None and await _name_taken(session, user.id, new_name, exclude=cat.id):
                raise ConflictError("A category with this name already exists", code="category_exists")
            pref.display_name = new_name.strip() if new_name else None
    else:
        if data.get("name") is not None:
            if await _name_taken(session, user.id, data["name"], exclude=cat.id):
                raise ConflictError("A category with this name already exists", code="category_exists")
            cat.name = data["name"].strip()
        for field in ("icon", "color", "is_fixed"):
            if field in data and (data[field] is not None or field != "is_fixed"):
                setattr(cat, field, data[field])
    if data.get("is_hidden") is not None:
        pref.is_hidden = data["is_hidden"]
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A category with this name already exists", code="category_exists") from exc
    await session.refresh(cat)
    await session.refresh(pref)
    return category_out(cat, pref)


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    reassign_to: Annotated[
        UUID | None,
        Query(description="Move its transactions and rules here (else they become uncategorised)"),
    ] = None,
) -> Response:
    """Delete one of the user's own categories (defaults can only be hidden). Its budgets go with it."""
    cat = await session.get(Category, category_id)
    if cat is None or cat.user_id != user.id:
        raise NotFoundError("Category not found")
    has_children = (
        await session.execute(select(Category.id).where(Category.parent_id == cat.id).limit(1))
    ).scalar_one_or_none()
    if has_children is not None:
        raise ConflictError("Delete or move its sub-categories first", code="category_has_children")
    if reassign_to is not None:
        target = await get_visible_category(session, user.id, reassign_to)
        if target.id == cat.id:
            raise DomainValidationError("Cannot reassign to the category being deleted")
        await session.execute(
            update(Transaction)
            .where(Transaction.user_id == user.id, Transaction.category_id == cat.id)
            .values(category_id=target.id)
        )
        await session.execute(
            update(MerchantRule)
            .where(MerchantRule.user_id == user.id, MerchantRule.category_id == cat.id)
            .values(category_id=target.id)
        )
    await session.delete(cat)
    await session.commit()
    return Response(status_code=204)

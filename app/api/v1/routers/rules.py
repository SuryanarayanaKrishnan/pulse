from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import DomainValidationError, NotFoundError
from app.models.merchant_rule import MerchantRule
from app.schemas.rule import (
    RuleApplyIn,
    RuleApplyOut,
    RuleCreate,
    RuleOut,
    RulePreviewIn,
    RulePreviewOut,
    RuleReorder,
    RuleUpdate,
)
from app.services.categorisation import compile_db_rule
from app.services.rule_engine import compile_rule
from app.services.rules import apply_to_history, preview, validate_rule_refs

router = APIRouter(prefix="/rules", tags=["rules"])

PRIORITY_STEP = 10


async def _get_rule(session: SessionDep, user_id: UUID, rule_id: UUID) -> MerchantRule:
    rule = (
        await session.execute(
            select(MerchantRule).where(MerchantRule.id == rule_id, MerchantRule.user_id == user_id)
        )
    ).scalar_one_or_none()
    if rule is None:
        raise NotFoundError("Rule not found")
    return rule


def _conditions_json(conditions: list[Any]) -> list[dict[str, object]]:
    return [c.model_dump(mode="json", exclude_none=True) for c in conditions]


@router.get("", response_model=list[RuleOut])
async def list_rules(user: CurrentUser, session: SessionDep) -> list[MerchantRule]:
    """All of the user's rules in evaluation order (lowest priority number first)."""
    rows = await session.execute(
        select(MerchantRule)
        .where(MerchantRule.user_id == user.id)
        .order_by(MerchantRule.priority, MerchantRule.created_at, MerchantRule.id)
    )
    return list(rows.scalars())


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(payload: RuleCreate, user: CurrentUser, session: SessionDep) -> RuleOut:
    conditions = _conditions_json(payload.conditions)
    await validate_rule_refs(session, user.id, payload.category_id, conditions)
    rule = MerchantRule(
        user_id=user.id,
        name=payload.name,
        priority=payload.priority,
        is_active=payload.is_active,
        conditions=conditions,
        category_id=payload.category_id,
        rename_merchant=payload.rename_merchant,
        mark_transfer=payload.mark_transfer,
        exclude_from_budget=payload.exclude_from_budget,
        tags=payload.tags,
    )
    session.add(rule)
    await session.flush()
    applied: int | None = None
    if payload.apply_to_existing:
        _, applied = await apply_to_history(session, user.id, compile_db_rule(rule))
    await session.commit()
    await session.refresh(rule)
    out = RuleOut.model_validate(rule)
    out.applied_count = applied
    return out


@router.post("/preview", response_model=RulePreviewOut)
async def preview_rule(payload: RulePreviewIn, user: CurrentUser, session: SessionDep) -> RulePreviewOut:
    """Which existing transactions would a rule with these conditions match? Nothing is changed."""
    conditions = _conditions_json(payload.conditions)
    await validate_rule_refs(session, user.id, payload.category_id, conditions)
    rule = compile_rule(
        conditions,
        category_id=payload.category_id,
        rename_merchant=payload.rename_merchant,
        mark_transfer=payload.mark_transfer,
        exclude_from_budget=payload.exclude_from_budget,
        tags=payload.tags,
    )
    return await preview(
        session,
        user.id,
        rule,
        only_uncategorised=payload.only_uncategorised,
        date_from=payload.date_from,
        limit=payload.limit,
    )


@router.post("/reorder", response_model=list[RuleOut])
async def reorder_rules(payload: RuleReorder, user: CurrentUser, session: SessionDep) -> list[MerchantRule]:
    """Set the evaluation order. `ids` must list every one of the user's rules, highest priority first."""
    rows = await session.execute(select(MerchantRule).where(MerchantRule.user_id == user.id))
    rules = {r.id: r for r in rows.scalars()}
    if len(set(payload.ids)) != len(payload.ids) or set(payload.ids) != set(rules):
        raise DomainValidationError("ids must list each of your rules exactly once", code="invalid_order")
    for i, rule_id in enumerate(payload.ids):
        rules[rule_id].priority = i * PRIORITY_STEP
    await session.commit()
    rows = await session.execute(
        select(MerchantRule).where(MerchantRule.user_id == user.id).order_by(MerchantRule.priority)
    )
    return list(rows.scalars())


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: UUID, user: CurrentUser, session: SessionDep) -> MerchantRule:
    return await _get_rule(session, user.id, rule_id)


@router.patch("/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: UUID, payload: RuleUpdate, user: CurrentUser, session: SessionDep
) -> MerchantRule:
    rule = await _get_rule(session, user.id, rule_id)
    data = payload.model_dump(exclude_unset=True)
    if "conditions" in data and payload.conditions is not None:
        rule.conditions = _conditions_json(payload.conditions)
        rule.merchant_key = None  # no longer the plain "merchant X" shortcut
    data.pop("conditions", None)
    for key, value in data.items():
        if value is None and key in ("priority", "is_active", "mark_transfer", "exclude_from_budget", "tags"):
            continue  # non-nullable columns: null means "leave unchanged"
        setattr(rule, key, value)
    await validate_rule_refs(session, user.id, rule.category_id, rule.conditions)
    has_action = rule.category_id or rule.rename_merchant or rule.mark_transfer
    if not (has_action or rule.exclude_from_budget or rule.tags):
        raise DomainValidationError("A rule needs at least one action", code="rule_without_action")
    await session.commit()
    await session.refresh(rule)
    return rule


@router.post("/{rule_id}/apply", response_model=RuleApplyOut)
async def apply_rule(
    rule_id: UUID, payload: RuleApplyIn, user: CurrentUser, session: SessionDep
) -> RuleApplyOut:
    """Retroactively apply this rule's actions to the user's past transactions that match it."""
    rule = await _get_rule(session, user.id, rule_id)
    matched, updated = await apply_to_history(
        session,
        user.id,
        compile_db_rule(rule),
        only_uncategorised=payload.only_uncategorised,
        date_from=payload.date_from,
    )
    await session.commit()
    return RuleApplyOut(matched=matched, updated=updated)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    rule = await _get_rule(session, user.id, rule_id)
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)

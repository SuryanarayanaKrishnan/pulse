from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.errors import DomainValidationError, NotFoundError
from app.models.merchant_rule import MerchantRule
from app.schemas.merchant_rule import MerchantRuleCreate, MerchantRuleOut
from app.services.categories import get_visible_category
from app.services.categorisation import apply_rule_to_existing, upsert_rule
from app.services.merchants import normalize_merchant

router = APIRouter(prefix="/merchant-rules", tags=["categorisation"])
# Kept for the "always categorise this merchant as X" shortcut; general rules live under /v1/rules.


@router.get("", response_model=list[MerchantRuleOut])
async def list_rules(user: CurrentUser, session: SessionDep) -> list[MerchantRule]:
    rows = await session.execute(
        select(MerchantRule)
        .where(MerchantRule.user_id == user.id, MerchantRule.merchant_key.is_not(None))
        .order_by(MerchantRule.merchant_key)
    )
    return list(rows.scalars())


@router.post("", response_model=MerchantRuleOut, status_code=201)
async def create_rule(payload: MerchantRuleCreate, user: CurrentUser, session: SessionDep) -> MerchantRule:
    """'Always categorise this merchant as X'. Upserts on the normalised merchant name."""
    await get_visible_category(session, user.id, payload.category_id)
    normalized = normalize_merchant(payload.merchant)
    if not normalized:
        raise DomainValidationError("Empty merchant", code="invalid_merchant")
    rule = await upsert_rule(session, user.id, normalized, payload.category_id)
    if payload.apply_to_existing:
        await apply_rule_to_existing(session, user.id, normalized, payload.category_id)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    rule = (
        await session.execute(
            select(MerchantRule).where(MerchantRule.id == rule_id, MerchantRule.user_id == user.id)
        )
    ).scalar_one_or_none()
    if rule is None:
        raise NotFoundError("Rule not found")
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)

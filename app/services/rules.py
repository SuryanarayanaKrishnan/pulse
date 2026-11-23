"""Database-facing parts of the rules feature: validation, preview, retroactive apply, suggestions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainValidationError
from app.models.account import Account
from app.models.merchant_rule import DEFAULT_RULE_PRIORITY, MerchantRule
from app.models.transaction import Transaction
from app.schemas.rule import (
    MerchantCondition,
    RulePreviewItem,
    RulePreviewOut,
    SuggestedRule,
)
from app.schemas.transaction import TransactionOut
from app.services.categories import get_visible_category, load_preferences
from app.services.categorisation import (
    SCAN_CAP,
    apply_rule_actions,
    default_category_ids,
    facts_of,
    iter_transactions,
)
from app.services.merchants import merchant_key
from app.services.rule_engine import CompiledRule


async def validate_rule_refs(
    session: AsyncSession, user_id: UUID, category_id: UUID | None, conditions: list[dict[str, object]]
) -> None:
    """Category must be visible to the user and account conditions must name the user's own accounts."""
    if category_id is not None:
        await get_visible_category(session, user_id, category_id)
    account_ids = {UUID(str(c["account_id"])) for c in conditions if c.get("field") == "account"}
    if account_ids:
        owned = set(
            (
                await session.execute(
                    select(Account.id).where(
                        Account.id.in_(account_ids), Account.user_id == user_id, Account.deleted_at.is_(None)
                    )
                )
            ).scalars()
        )
        if owned != account_ids:
            raise DomainValidationError("Unknown account in conditions", code="invalid_account")


async def preview(
    session: AsyncSession,
    user_id: UUID,
    rule: CompiledRule,
    *,
    only_uncategorised: bool,
    date_from: datetime | None,
    limit: int,
) -> RulePreviewOut:
    transfer = (await default_category_ids(session)).get("transfer")
    target = rule.category_id or (transfer if rule.mark_transfer else None)
    matched = scanned = 0
    items: list[RulePreviewItem] = []
    async for txn in iter_transactions(
        session, user_id, date_from=date_from, only_uncategorised=only_uncategorised
    ):
        scanned += 1
        if not rule.matches(facts_of(txn)):
            continue
        matched += 1
        if len(items) < limit:
            items.append(
                RulePreviewItem(
                    transaction=TransactionOut.model_validate(txn),
                    category_would_change=target is not None and txn.category_id != target,
                )
            )
    return RulePreviewOut(
        matched_count=matched, scanned_count=scanned, truncated=scanned >= SCAN_CAP, items=items
    )


async def apply_to_history(
    session: AsyncSession,
    user_id: UUID,
    rule: CompiledRule,
    *,
    only_uncategorised: bool = False,
    date_from: datetime | None = None,
) -> tuple[int, int]:
    """Apply the rule's actions to every matching past transaction. Returns (matched, updated)."""
    transfer = (await default_category_ids(session)).get("transfer")
    matched = updated = 0
    async for txn in iter_transactions(
        session, user_id, date_from=date_from, only_uncategorised=only_uncategorised
    ):
        if not rule.matches(facts_of(txn)):
            continue
        matched += 1
        if apply_rule_actions(txn, rule, transfer):
            updated += 1
    await session.flush()
    return matched, updated


async def suggest_rule(
    session: AsyncSession, user_id: UUID, txn: Transaction, category_id: UUID
) -> SuggestedRule | None:
    """After a manual re-categorisation: "Always categorise <merchant> as <category>?"

    None when the transaction has no merchant or an equivalent active rule already exists.
    """
    if not txn.merchant_normalized or merchant_key(txn.merchant_normalized) is None:
        return None
    key = merchant_key(txn.merchant_normalized)
    existing = await session.execute(
        select(MerchantRule).where(
            MerchantRule.user_id == user_id,
            MerchantRule.is_active.is_(True),
            MerchantRule.category_id == category_id,
        )
    )
    for rule in existing.scalars():
        if len(rule.conditions) == 1:
            c = rule.conditions[0]
            if (
                c.get("field") == "merchant"
                and c.get("op") == "equals"
                and merchant_key(str(c.get("value"))) == key
            ):
                return None
    count = (
        await session.execute(
            select(func.count()).where(
                Transaction.user_id == user_id,
                Transaction.deleted_at.is_(None),
                Transaction.id != txn.id,
                func.lower(Transaction.merchant_normalized) == txn.merchant_normalized.strip().lower(),
                Transaction.category_id.is_distinct_from(category_id),
            )
        )
    ).scalar_one()
    category = await get_visible_category(session, user_id, category_id)
    pref = (await load_preferences(session, user_id)).get(category_id)
    cat_name = pref.display_name if pref and pref.display_name else category.name
    return SuggestedRule(
        name=f"{txn.merchant_normalized} -> {cat_name}"[:120],
        priority=DEFAULT_RULE_PRIORITY,
        conditions=[MerchantCondition(field="merchant", op="equals", value=txn.merchant_normalized)],
        category_id=category_id,
        matching_count=int(count),
    )

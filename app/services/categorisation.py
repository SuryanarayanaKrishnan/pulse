"""Deterministic categorisation: user rules first, then the curated merchant dictionary.

``CategoryResolver`` loads the user's rules and the system category ids once, so batch paths
(statement lines) do not query per row. Used by every ingest path (SMS/notification/shortcut,
statements, manual entry).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Direction, Transaction
from app.services.merchants import guess_category_name, match_entry, merchant_key, normalize_merchant


@dataclass(frozen=True, slots=True)
class Enrichment:
    merchant_normalized: str | None
    category_id: UUID | None
    source: str | None  # "rule" | "dictionary" | None


class CategoryResolver:
    def __init__(self, rules: dict[str, UUID], system_categories: dict[str, UUID]) -> None:
        self.rules = rules
        self.system_categories = system_categories

    @classmethod
    async def load(cls, session: AsyncSession, user_id: UUID) -> "CategoryResolver":
        rules = dict(
            (
                await session.execute(
                    select(MerchantRule.merchant_key, MerchantRule.category_id).where(
                        MerchantRule.user_id == user_id
                    )
                )
            )
            .tuples()
            .all()
        )
        system = dict(
            (await session.execute(select(Category.name, Category.id).where(Category.user_id.is_(None))))
            .tuples()
            .all()
        )
        return cls(rules, system)

    def enrich(self, merchant_raw: str | None, direction: Direction | None = None) -> Enrichment:
        normalized = normalize_merchant(merchant_raw, direction)
        key = merchant_key(normalized)
        if key and key in self.rules:
            return Enrichment(normalized, self.rules[key], "rule")
        name = guess_category_name(merchant_raw, direction)
        if name and name in self.system_categories:
            return Enrichment(normalized, self.system_categories[name], "dictionary")
        return Enrichment(normalized, None, None)


async def upsert_rule(
    session: AsyncSession, user_id: UUID, merchant_normalized: str, category_id: UUID
) -> MerchantRule:
    key = merchant_key(merchant_normalized)
    assert key
    stmt = (
        pg_insert(MerchantRule)
        .values(user_id=user_id, merchant_key=key, category_id=category_id)
        .on_conflict_do_update(
            constraint="uq_merchant_rules_user_id_merchant_key", set_={"category_id": category_id}
        )
        .returning(MerchantRule.id)
    )
    rule_id = (await session.execute(stmt)).scalar_one()
    rule = await session.get(MerchantRule, rule_id, populate_existing=True)
    assert rule is not None
    return rule


async def apply_rule_to_existing(
    session: AsyncSession, user_id: UUID, merchant_normalized: str, category_id: UUID
) -> int:
    """Re-categorise the user's existing transactions for this merchant. Returns rows changed."""
    from sqlalchemy import func

    result = await session.execute(
        update(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            func.lower(Transaction.merchant_normalized) == merchant_normalized.strip().lower(),
            Transaction.category_id.is_distinct_from(category_id),
        )
        .values(category_id=category_id)
    )
    return result.rowcount or 0  # type: ignore[attr-defined]


__all__ = ["CategoryResolver", "Enrichment", "apply_rule_to_existing", "match_entry", "upsert_rule"]

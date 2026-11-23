"""Deterministic classification: the user's rules first, then the curated merchant dictionary.

``CategoryResolver`` loads the user's active rules and the default category ids once, so batch paths
(statement lines) do not query per row. Used by every ingest path (SMS/notification/shortcut,
statements, manual entry). The rules live in ``merchant_rules`` (see ``models.merchant_rule``); the
matching engine is ``services.rule_engine``.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.merchant_rule import AUTO_RULE_PRIORITY, MerchantRule
from app.models.transaction import Direction, Transaction, TxnSource
from app.services.merchants import guess_category_slug, match_entry, merchant_key, normalize_merchant
from app.services.rule_engine import CompiledRule, RuleOutcome, TxnFacts, compile_rule, evaluate

SCAN_CAP = 50_000  # rows examined by preview/apply; a longer history is reported as truncated
SCAN_BATCH = 500


@dataclass(frozen=True, slots=True)
class Enrichment:
    merchant_normalized: str | None
    category_id: UUID | None
    source: str | None  # "rule" | "dictionary" | None
    exclude_from_budget: bool = False
    tags: tuple[str, ...] = ()
    rule_id: UUID | None = None  # the rule that decided the category (else the first one that matched)
    matched_rule_ids: tuple[UUID, ...] = field(default=())


def compile_db_rule(rule: MerchantRule) -> CompiledRule:
    return compile_rule(
        rule.conditions,
        id=rule.id,
        priority=rule.priority,
        category_id=rule.category_id,
        rename_merchant=rule.rename_merchant,
        mark_transfer=rule.mark_transfer,
        exclude_from_budget=rule.exclude_from_budget,
        tags=rule.tags,
    )


class CategoryResolver:
    def __init__(self, rules: Sequence[CompiledRule], system_categories: dict[str, UUID]) -> None:
        self.rules = list(rules)
        self.system_categories = system_categories

    @classmethod
    async def load(cls, session: AsyncSession, user_id: UUID) -> "CategoryResolver":
        rule_rows = await session.execute(
            select(MerchantRule)
            .where(MerchantRule.user_id == user_id, MerchantRule.is_active.is_(True))
            .order_by(MerchantRule.priority, MerchantRule.created_at, MerchantRule.id)
        )
        return cls([compile_db_rule(r) for r in rule_rows.scalars()], await default_category_ids(session))

    def apply_outcome(
        self, outcome: RuleOutcome | None, normalized: str | None, dictionary_slug: str | None
    ) -> Enrichment:
        category = outcome.category_id if outcome else None
        if category is None and outcome and outcome.mark_transfer:
            category = self.system_categories.get("transfer")
        source = "rule" if category is not None and outcome else None
        if category is None and dictionary_slug and dictionary_slug in self.system_categories:
            category, source = self.system_categories[dictionary_slug], "dictionary"
        if outcome is None:
            return Enrichment(normalized, category, source)
        return Enrichment(
            outcome.rename_merchant or normalized,
            category,
            source,
            exclude_from_budget=outcome.exclude_from_budget or outcome.mark_transfer,
            tags=tuple(outcome.tags),
            rule_id=outcome.first_rule_id,
            matched_rule_ids=tuple(outcome.rule_ids),
        )

    def enrich(
        self,
        merchant_raw: str | None,
        direction: Direction | None = None,
        *,
        account_id: UUID | None = None,
        amount_minor: int | None = None,
        source: TxnSource | None = None,
        notes: str | None = None,
    ) -> Enrichment:
        normalized = normalize_merchant(merchant_raw, direction)
        facts = TxnFacts(merchant_raw, normalized, notes, amount_minor, direction, account_id, source)
        outcome = evaluate(self.rules, facts)
        return self.apply_outcome(outcome, normalized, guess_category_slug(merchant_raw, direction))


async def default_category_ids(session: AsyncSession) -> dict[str, UUID]:
    rows = await session.execute(
        select(Category.slug, Category.id).where(Category.user_id.is_(None), Category.slug.is_not(None))
    )
    return {slug: cat_id for slug, cat_id in rows if slug}


def facts_of(txn: Transaction) -> TxnFacts:
    return TxnFacts(
        txn.merchant_raw,
        txn.merchant_normalized,
        txn.notes,
        txn.amount_minor,
        txn.direction,
        txn.account_id,
        txn.source,
    )


def apply_enrichment(
    txn: Transaction, e: Enrichment, *, keep_category: bool = False, set_merchant: bool = True
) -> None:
    """Copy a rule/dictionary result onto a transaction (an explicit category can be kept)."""
    if not keep_category and e.category_id is not None:
        txn.category_id = e.category_id
    if set_merchant and e.merchant_normalized:
        txn.merchant_normalized = e.merchant_normalized
    if e.exclude_from_budget:
        txn.exclude_from_budget = True
    if e.tags:
        txn.tags = list(dict.fromkeys([*(txn.tags or []), *e.tags]))
    if e.rule_id is not None:
        txn.applied_rule_id = e.rule_id


def apply_rule_actions(txn: Transaction, rule: CompiledRule, transfer_category_id: UUID | None) -> bool:
    """Apply one rule's actions to a transaction. Returns True if anything changed."""
    before = (
        txn.category_id,
        txn.merchant_normalized,
        txn.exclude_from_budget,
        tuple(txn.tags or ()),
        txn.applied_rule_id,
    )
    if rule.category_id is not None:
        txn.category_id = rule.category_id
    elif rule.mark_transfer and transfer_category_id is not None:
        txn.category_id = transfer_category_id
    if rule.rename_merchant:
        txn.merchant_normalized = rule.rename_merchant
    if rule.exclude_from_budget or rule.mark_transfer:
        txn.exclude_from_budget = True
    if rule.tags:
        txn.tags = list(dict.fromkeys([*(txn.tags or []), *rule.tags]))
    if rule.id is not None:
        txn.applied_rule_id = rule.id
    after = (
        txn.category_id,
        txn.merchant_normalized,
        txn.exclude_from_budget,
        tuple(txn.tags or ()),
        txn.applied_rule_id,
    )
    return before != after


async def iter_transactions(
    session: AsyncSession,
    user_id: UUID,
    *,
    date_from: datetime | None = None,
    only_uncategorised: bool = False,
    batch: int = SCAN_BATCH,
    cap: int = SCAN_CAP,
) -> AsyncIterator[Transaction]:
    """The user's live transactions, newest first, in keyset-paged batches (stable while rows are updated)."""
    last: tuple[datetime, UUID] | None = None
    seen = 0
    while seen < cap:
        stmt = select(Transaction).where(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
        if date_from is not None:
            stmt = stmt.where(Transaction.posted_at >= date_from)
        if only_uncategorised:
            stmt = stmt.where(Transaction.category_id.is_(None))
        if last is not None:
            stmt = stmt.where(tuple_(Transaction.posted_at, Transaction.id) < tuple_(*last))
        rows = list(
            (
                await session.execute(
                    stmt.order_by(Transaction.posted_at.desc(), Transaction.id.desc()).limit(
                        min(batch, cap - seen)
                    )
                )
            ).scalars()
        )
        if not rows:
            return
        for r in rows:
            yield r
        seen += len(rows)
        last = (rows[-1].posted_at, rows[-1].id)


async def upsert_rule(
    session: AsyncSession, user_id: UUID, merchant_normalized: str, category_id: UUID
) -> MerchantRule:
    """The learnt shortcut "always categorise this merchant as X" (a merchant-equals rule)."""
    key = merchant_key(merchant_normalized)
    assert key
    stmt = (
        pg_insert(MerchantRule)
        .values(
            user_id=user_id,
            merchant_key=key,
            category_id=category_id,
            priority=AUTO_RULE_PRIORITY,
            conditions=[{"field": "merchant", "op": "equals", "value": key}],
        )
        .on_conflict_do_update(
            constraint="uq_merchant_rules_user_id_merchant_key",
            set_={"category_id": category_id, "is_active": True},
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


__all__ = [
    "CategoryResolver",
    "Enrichment",
    "apply_enrichment",
    "apply_rule_actions",
    "apply_rule_to_existing",
    "compile_db_rule",
    "default_category_ids",
    "facts_of",
    "iter_transactions",
    "match_entry",
    "upsert_rule",
]

"""Pure classification-rule engine: conditions are AND-ed, rules are tried in priority order.

Nothing here touches the database. A rule's conditions are stored as JSON (see ``schemas.rule``);
``compile_rule`` turns them into predicates once so batch paths (statements, preview/apply over a
whole history) do not re-parse per transaction.

Semantics
- ``merchant`` looks at the normalised merchant and the raw descriptor, ``description`` at the raw
  descriptor and the notes; all text matching is case-insensitive.
- Across matching rules the first one (lowest priority number) that sets a field wins that field;
  tags accumulate. So a high-priority rule can set the category while a later one only adds a tag.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.models.transaction import Direction, TxnSource
from app.services.merchants import merchant_key

MAX_REGEX_LEN = 200
MAX_SUBJECT_LEN = 500  # text handed to a user-supplied regex is truncated: bounds worst-case backtracking
# A quantified group that itself contains a quantifier, e.g. (a+)+ or (.*)* - the classic ReDoS shape.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")


def check_regex(pattern: str) -> re.Pattern[str]:
    """Compile a user-supplied pattern or raise ValueError (overlong or nested-quantifier patterns)."""
    if len(pattern) > MAX_REGEX_LEN:
        raise ValueError(f"regex is longer than {MAX_REGEX_LEN} characters")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError("regex has nested quantifiers, which can take exponential time")
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc


@dataclass(frozen=True, slots=True)
class TxnFacts:
    merchant_raw: str | None
    merchant_normalized: str | None
    notes: str | None
    amount_minor: int | None
    direction: Direction | None
    account_id: UUID | None
    source: TxnSource | None


Predicate = Callable[[TxnFacts], bool]


def _texts(*values: str | None) -> list[str]:
    return [v.lower()[:MAX_SUBJECT_LEN] for v in values if v]


def _text_predicate(field_name: str, op: str, value: str) -> Predicate:
    needle = value.lower()
    regex = check_regex(value) if op == "regex" else None

    def subjects(f: TxnFacts) -> list[str]:
        if field_name == "merchant":
            return _texts(f.merchant_normalized, f.merchant_raw)
        return _texts(f.merchant_raw, f.notes)

    def predicate(f: TxnFacts) -> bool:
        subs = subjects(f)
        if op == "contains":
            return any(needle in s for s in subs)
        if op == "equals":
            if field_name == "merchant":
                key = merchant_key(value)
                return any(merchant_key(s) == key for s in subs)
            return any(s.strip() == needle.strip() for s in subs)
        assert regex is not None
        return any(regex.search(s) for s in subs)

    return predicate


def _amount_predicate(c: Mapping[str, Any]) -> Predicate:
    op = c["op"]

    def predicate(f: TxnFacts) -> bool:
        a = f.amount_minor
        if a is None:
            return False
        if op == "equals":
            return bool(a == c["value_minor"])
        if op == "gte":
            return bool(a >= c["value_minor"])
        if op == "lte":
            return bool(a <= c["value_minor"])
        return bool(c["min_minor"] <= a <= c["max_minor"])

    return predicate


def compile_condition(c: Mapping[str, Any]) -> Predicate:
    kind = c["field"]
    if kind in ("merchant", "description"):
        return _text_predicate(kind, c["op"], c["value"])
    if kind == "amount":
        return _amount_predicate(c)
    if kind == "direction":
        want = Direction(c["value"])
        return lambda f: f.direction == want
    if kind == "account":
        account = UUID(str(c["account_id"]))
        return lambda f: f.account_id == account
    if kind == "source":
        src = TxnSource(c["value"])
        return lambda f: f.source == src
    raise ValueError(f"unknown condition field {kind!r}")


@dataclass(frozen=True, slots=True)
class CompiledRule:
    id: UUID | None
    priority: int
    predicates: tuple[Predicate, ...]
    category_id: UUID | None = None
    rename_merchant: str | None = None
    mark_transfer: bool = False
    exclude_from_budget: bool = False
    tags: tuple[str, ...] = ()

    def matches(self, facts: TxnFacts) -> bool:
        return bool(self.predicates) and all(p(facts) for p in self.predicates)


def compile_rule(
    conditions: Iterable[Mapping[str, Any]],
    *,
    id: UUID | None = None,
    priority: int = 0,
    category_id: UUID | None = None,
    rename_merchant: str | None = None,
    mark_transfer: bool = False,
    exclude_from_budget: bool = False,
    tags: Sequence[str] = (),
) -> CompiledRule:
    return CompiledRule(
        id=id,
        priority=priority,
        predicates=tuple(compile_condition(c) for c in conditions),
        category_id=category_id,
        rename_merchant=rename_merchant,
        mark_transfer=mark_transfer,
        exclude_from_budget=exclude_from_budget,
        tags=tuple(tags),
    )


def merchant_equals_rule(
    key: str, category_id: UUID, *, id: UUID | None = None, priority: int = 1000
) -> CompiledRule:
    return compile_rule(
        [{"field": "merchant", "op": "equals", "value": key}],
        id=id,
        priority=priority,
        category_id=category_id,
    )


@dataclass(slots=True)
class RuleOutcome:
    category_id: UUID | None = None
    rename_merchant: str | None = None
    mark_transfer: bool = False
    exclude_from_budget: bool = False
    tags: list[str] = field(default_factory=list)
    rule_ids: list[UUID] = field(default_factory=list)
    first_rule_id: UUID | None = None  # the rule that decided the category, else the first match


def evaluate(rules: Sequence[CompiledRule], facts: TxnFacts) -> RuleOutcome | None:
    """Run ``rules`` (already in priority order); None when nothing matched."""
    outcome: RuleOutcome | None = None
    for rule in rules:
        if not rule.matches(facts):
            continue
        if outcome is None:
            outcome = RuleOutcome(first_rule_id=rule.id)
        if rule.id is not None:
            outcome.rule_ids.append(rule.id)
        if outcome.category_id is None and rule.category_id is not None:
            outcome.category_id = rule.category_id
            outcome.first_rule_id = rule.id
        if outcome.rename_merchant is None and rule.rename_merchant:
            outcome.rename_merchant = rule.rename_merchant
        outcome.mark_transfer = outcome.mark_transfer or rule.mark_transfer
        outcome.exclude_from_budget = outcome.exclude_from_budget or rule.exclude_from_budget
        for t in rule.tags:
            if t not in outcome.tags:
                outcome.tags.append(t)
    return outcome

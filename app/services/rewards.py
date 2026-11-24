"""Rewards engine: pure, deterministic card-reward maths (no LLM, no network, no database).

A card's ``reward_structure`` (JSON on ``cards_catalog``)::

    {"unit": "cashback" | "points",            # cashback units are AED, points are the programme's points
     "point_value_aed": 0.025,                 # AED value of one point (1 for cashback)
     "base_rate": 0.01,                        # units earned per AED spent
     "rules": [{"name": "Dining", "categories": ["dining"], "merchants": ["talabat"],
                "rate": 0.05, "monthly_cap_units": 100}],
     "excluded_categories": ["fees", "cc_payment", "transfer"],
     "min_monthly_spend_minor": 200000,        # earn nothing in a cycle below this spend
     "fee_waiver_spend_minor": 2000000}        # annual spend that waives the annual fee

Rule choice: a merchant match beats a category match beats the base rate; an excluded category earns
nothing. Caps are per rule per statement cycle (units: AED for cashback, points otherwise).
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class RuleSpec:
    name: str
    rate: Decimal
    categories: frozenset[str] = frozenset()
    merchants: tuple[str, ...] = ()
    cap_units: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CardSpec:
    unit: str
    point_value_aed: Decimal
    base_rate: Decimal
    rules: tuple[RuleSpec, ...] = ()
    excluded_categories: frozenset[str] = frozenset()
    min_monthly_spend_minor: int = 0
    fee_waiver_spend_minor: int | None = None

    def value_minor(self, units: Decimal) -> int:
        return int((units * self.point_value_aed * 100).to_integral_value())


def parse_structure(data: dict[str, Any]) -> CardSpec:
    """Validate and load a reward_structure (raises ValueError on garbage)."""
    unit = data.get("unit", "cashback")
    if unit not in ("cashback", "points"):
        raise ValueError("unit must be cashback or points")
    value = Decimal(str(data.get("point_value_aed", 1)))
    base = Decimal(str(data.get("base_rate", 0)))
    if value <= 0 or base < 0:
        raise ValueError("point_value_aed must be positive and base_rate non-negative")
    rules: list[RuleSpec] = []
    for r in data.get("rules", []):
        rate = Decimal(str(r["rate"]))
        if rate < 0 or not (r.get("categories") or r.get("merchants")):
            raise ValueError(f"rule {r.get('name')!r} needs a rate and categories or merchants")
        cap = r.get("monthly_cap_units")
        rules.append(
            RuleSpec(
                name=str(r["name"]),
                rate=rate,
                categories=frozenset(r.get("categories", [])),
                merchants=tuple(m.lower() for m in r.get("merchants", [])),
                cap_units=Decimal(str(cap)) if cap is not None else None,
            )
        )
    return CardSpec(
        unit=unit,
        point_value_aed=value,
        base_rate=base,
        rules=tuple(rules),
        excluded_categories=frozenset(data.get("excluded_categories", [])),
        min_monthly_spend_minor=int(data.get("min_monthly_spend_minor", 0)),
        fee_waiver_spend_minor=data.get("fee_waiver_spend_minor"),
    )


BASE_RULE = "Base"


def select_rule(spec: CardSpec, category: str | None, merchant_key: str | None) -> RuleSpec | None:
    """The rule that earns for this spend: None when the category is excluded (earns nothing)."""
    if category in spec.excluded_categories:
        return None
    if merchant_key:
        for r in spec.rules:
            if any(m in merchant_key for m in r.merchants):
                return r
    if category:
        for r in spec.rules:
            if category in r.categories:
                return r
    return RuleSpec(BASE_RULE, spec.base_rate)


@dataclass(frozen=True, slots=True)
class Spend:
    amount_minor: int
    category: str | None = None
    merchant_key: str | None = None


@dataclass(slots=True)
class CapUsage:
    rule: str
    cap_units: Decimal | None
    used_units: Decimal = ZERO

    @property
    def remaining_units(self) -> Decimal | None:
        return None if self.cap_units is None else max(ZERO, self.cap_units - self.used_units)

    @property
    def pct_used(self) -> Decimal | None:
        if not self.cap_units:
            return None
        return min(Decimal(100), self.used_units / self.cap_units * 100)


@dataclass(slots=True)
class CycleResult:
    spend_minor: int = 0
    earned_units: Decimal = ZERO
    usage: dict[str, CapUsage] = field(default_factory=dict)
    min_spend_met: bool = True

    def earned_value_minor(self, spec: CardSpec) -> int:
        return spec.value_minor(self.earned_units) if self.min_spend_met else 0


def earn(spec: CardSpec, spend: Spend, usage: dict[str, CapUsage]) -> tuple[Decimal, RuleSpec | None]:
    """Units earned by one spend given what the cycle has already used (mutates ``usage``)."""
    rule = select_rule(spec, spend.category, spend.merchant_key)
    if rule is None or spend.amount_minor <= 0:
        return ZERO, rule
    units = Decimal(spend.amount_minor) / 100 * rule.rate
    state = usage.setdefault(rule.name, CapUsage(rule.name, rule.cap_units))
    if state.cap_units is not None:
        units = min(units, max(ZERO, state.cap_units - state.used_units))
    state.used_units += units
    return units, rule


def compute_cycle(spec: CardSpec, spends: list[Spend]) -> CycleResult:
    """Apply a cycle's spends in order (caps bind chronologically)."""
    res = CycleResult()
    for sp in spends:
        res.spend_minor += max(0, sp.amount_minor)
        units, _ = earn(spec, sp, res.usage)
        res.earned_units += units
    res.min_spend_met = res.spend_minor >= spec.min_monthly_spend_minor
    return res


@dataclass(frozen=True, slots=True)
class Quote:
    units: Decimal
    value_minor: int
    rule: str | None
    headroom_units: Decimal | None  # cap left on the rule before this spend (None = uncapped)
    capped: bool  # this spend would hit (part of) the cap


def quote(spec: CardSpec, state: CycleResult, spend: Spend) -> Quote:
    """What would this spend earn on top of the cycle so far (state is not modified)."""
    rule = select_rule(spec, spend.category, spend.merchant_key)
    if rule is None or spend.amount_minor <= 0:
        return Quote(ZERO, 0, None, None, False)
    used = state.usage.get(rule.name)
    headroom = None
    if rule.cap_units is not None:
        headroom = max(ZERO, rule.cap_units - (used.used_units if used else ZERO))
    full = Decimal(spend.amount_minor) / 100 * rule.rate
    units = full if headroom is None else min(full, headroom)
    return Quote(units, spec.value_minor(units), rule.name, headroom, units < full)


# ------------------------------------------------------------------------------------------------
# Cycle dates
# ------------------------------------------------------------------------------------------------
def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def cycle_bounds(today: date, statement_day: int | None) -> tuple[date, date]:
    """(first day, last day) of the reward cycle containing ``today``.

    With a statement day the cycle runs from the day after one statement date to the next statement date;
    without one it is the calendar month.
    """
    if statement_day is None:
        return today.replace(day=1), _clamp(today.year, today.month, 31)
    this = _clamp(today.year, today.month, statement_day)
    last = (
        this
        if this < today
        else (_clamp(today.year - (today.month == 1), (today.month - 2) % 12 + 1, statement_day))
    )
    nxt_month = last.month % 12 + 1
    nxt = _clamp(last.year + (last.month == 12), nxt_month, statement_day)
    return last + timedelta(days=1), nxt

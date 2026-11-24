"""Financial health score (pure): four components scored 0-100, combined by weight.

Components with no data are left out and the remaining weights renormalised; with less than half the
weight available there is no overall score (we say so rather than guess).
"""

from dataclasses import dataclass

WEIGHTS = {"savings_rate": 30, "credit_utilisation": 25, "on_time_payments": 25, "emergency_runway": 20}
LABELS = {
    "savings_rate": "saving",
    "credit_utilisation": "credit use",
    "on_time_payments": "paying cards on time",
    "emergency_runway": "emergency cushion",
}
MIN_COVERAGE = 0.5
TARGET_SAVINGS_RATE = 0.20
TARGET_RUNWAY_MONTHS = 6.0
UTILISATION_CURVE = ((0.0, 100.0), (10.0, 100.0), (30.0, 70.0), (50.0, 30.0), (80.0, 0.0))


@dataclass(frozen=True, slots=True)
class Component:
    key: str
    weight: int
    score: int | None
    value: float | None
    detail: str


def _clamp(x: float) -> int:
    return round(max(0.0, min(100.0, x)))


def savings_component(rate: float | None) -> Component:
    if rate is None:
        return Component(
            "savings_rate", WEIGHTS["savings_rate"], None, None, "No income recorded in recent months"
        )
    return Component(
        "savings_rate", WEIGHTS["savings_rate"], _clamp(rate / TARGET_SAVINGS_RATE * 100), round(rate, 4),
        f"You kept {rate * 100:.0f}% of income over 3 months (target {TARGET_SAVINGS_RATE * 100:.0f}%)",
    )  # fmt: skip


def utilisation_component(owed_minor: int | None, limit_minor: int | None) -> Component:
    w = WEIGHTS["credit_utilisation"]
    if not limit_minor or owed_minor is None:
        return Component("credit_utilisation", w, None, None, "No credit-card limit and balance known")
    pct = max(0, owed_minor) / limit_minor * 100
    score = UTILISATION_CURVE[-1][1]
    for (x0, y0), (x1, y1) in zip(UTILISATION_CURVE, UTILISATION_CURVE[1:], strict=False):
        if pct <= x1:
            score = y0 + (y1 - y0) * (pct - x0) / (x1 - x0)
            break
    return Component(
        "credit_utilisation", w, _clamp(score), round(pct, 1), f"Cards are {pct:.0f}% used overall"
    )


def on_time_component(paid_on_time: int, total: int) -> Component:
    w = WEIGHTS["on_time_payments"]
    if total == 0:
        return Component("on_time_payments", w, None, None, "No past card statements to judge")
    return Component(
        "on_time_payments", w, _clamp(paid_on_time / total * 100), round(paid_on_time / total, 4),
        f"{paid_on_time} of {total} recent card statements had at least the minimum paid by the due date",
    )  # fmt: skip


def runway_component(liquid_minor: int | None, monthly_spend_minor: int | None) -> Component:
    w = WEIGHTS["emergency_runway"]
    if liquid_minor is None or not monthly_spend_minor:
        return Component("emergency_runway", w, None, None, "Need a known balance and some spending history")
    months = max(0, liquid_minor) / monthly_spend_minor
    return Component(
        "emergency_runway", w, _clamp(months / TARGET_RUNWAY_MONTHS * 100), round(months, 1),
        f"Your cash covers about {months:.1f} months of spending (target {TARGET_RUNWAY_MONTHS:.0f})",
    )  # fmt: skip


def band(score: int) -> str:
    return (
        "strong" if score >= 80 else "good" if score >= 60 else "fair" if score >= 40 else "needs_attention"
    )


@dataclass(slots=True)
class Health:
    score: int | None
    band: str | None
    coverage: float
    components: list[Component]
    summary: str


def compute(components: list[Component]) -> Health:
    total = sum(c.weight for c in components)
    avail = [c for c in components if c.score is not None]
    weight = sum(c.weight for c in avail)
    coverage = round(weight / total, 2) if total else 0.0
    if not avail or coverage < MIN_COVERAGE:
        return Health(
            None, None, coverage, components, "Not enough data yet for a score: add balances and statements."
        )
    score = round(sum((c.score or 0) * c.weight for c in avail) / weight)
    best = max(avail, key=lambda c: (c.score or 0, c.weight))
    worst = min(avail, key=lambda c: (c.score or 0, -c.weight))
    b = band(score)
    label = b.replace("_", " ")
    if best.key == worst.key:
        summary = f"Health score {score} ({label}), based on {LABELS[best.key]} only."
    else:
        summary = (
            f"Health score {score} ({label}): strongest on {LABELS[best.key]}, "
            f"most room to improve on {LABELS[worst.key]}."
        )
    return Health(score, b, coverage, components, summary)

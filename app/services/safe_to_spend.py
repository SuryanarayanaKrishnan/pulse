"""Safe-to-spend: what is free after everything already spoken for (pure)."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Commitment:
    kind: str  # bill | loan | card_due | goal
    label: str
    amount_minor: int
    due_on: date | None = None


@dataclass(slots=True)
class SafeToSpend:
    liquid_minor: int | None
    committed_minor: int
    safe_minor: int | None
    per_day_minor: int | None
    days_left: int
    status: str  # ok | shortfall | unknown
    commitments: list[Commitment] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)


def compute(
    liquid_minor: int | None, commitments: list[Commitment], today: date, horizon_end: date
) -> SafeToSpend:
    days_left = max(1, (horizon_end - today).days)
    by_kind: dict[str, int] = {}
    for c in commitments:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + c.amount_minor
    total = sum(by_kind.values())
    if liquid_minor is None:
        return SafeToSpend(None, total, None, None, days_left, "unknown", commitments, by_kind)
    safe = liquid_minor - total
    return SafeToSpend(
        liquid_minor, total, safe, max(0, safe) // days_left, days_left,
        "ok" if safe >= 0 else "shortfall", commitments, by_kind,
    )  # fmt: skip

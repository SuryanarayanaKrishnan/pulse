"""Monthly cashflow and the daily balance projection (pure)."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

NOT_INCOME = frozenset({"transfer", "cc_payment"})
NOT_SPEND = frozenset({"transfer", "cc_payment", "savings"})


@dataclass(frozen=True, slots=True)
class Flow:
    day: date
    amount_minor: int
    is_credit: bool
    slug: str | None
    excluded: bool = False  # exclude_from_budget


@dataclass(slots=True)
class MonthFlow:
    month: date
    income_minor: int = 0
    spend_minor: int = 0
    saved_minor: int = 0

    @property
    def net_minor(self) -> int:
        return self.income_minor - self.spend_minor

    @property
    def savings_rate(self) -> float | None:
        return round(self.net_minor / self.income_minor, 4) if self.income_minor > 0 else None


def month_start(d: date) -> date:
    return d.replace(day=1)


def months_back(today: date, n: int) -> list[date]:
    """First days of the n months ending with the current month, oldest first."""
    idx = today.year * 12 + today.month - 1
    return [date((idx - i) // 12, (idx - i) % 12 + 1, 1) for i in range(n - 1, -1, -1)]


def is_income(f: Flow) -> bool:
    return f.is_credit and f.slug not in NOT_INCOME and not f.excluded


def is_spend(f: Flow) -> bool:
    return not f.is_credit and f.slug not in NOT_SPEND and not f.excluded


def monthly_cashflow(flows: list[Flow], today: date, n: int = 12) -> list[MonthFlow]:
    rows = {m: MonthFlow(m) for m in months_back(today, n)}
    for f in flows:
        row = rows.get(month_start(f.day))
        if row is None:
            continue
        if is_income(f):
            row.income_minor += f.amount_minor
        elif is_spend(f):
            row.spend_minor += f.amount_minor
        elif not f.is_credit and f.slug == "savings" and not f.excluded:
            row.saved_minor += f.amount_minor
    return list(rows.values())


@dataclass(frozen=True, slots=True)
class Event:
    day: date
    label: str
    amount_minor: int  # negative = money out
    kind: str  # bill | loan | card_due | salary


@dataclass(slots=True)
class ProjectedDay:
    day: date
    balance_minor: int
    events: list[Event] = field(default_factory=list)


def project_balance(
    start_balance_minor: int, today: date, days: int, events: list[Event], daily_variable_minor: int
) -> list[ProjectedDay]:
    """Day-by-day balance: each day spends the average variable amount, then scheduled events land."""
    by_day: dict[date, list[Event]] = defaultdict(list)
    for e in events:
        by_day[e.day].append(e)
    out: list[ProjectedDay] = []
    bal = start_balance_minor
    for i in range(days + 1):
        d = today + timedelta(days=i)
        if i > 0:
            bal -= daily_variable_minor
        evs = by_day.get(d, [])
        bal += sum(e.amount_minor for e in evs)
        out.append(ProjectedDay(d, bal, evs))
    return out

"""Credit-card due dates and pay-by nudges (pure).

Total due is the statement's closing balance (what was owed at the statement date); minimum due is the
smaller figure printed on it. They are reported separately and both shrink by payments made since the
statement date. Interest rates and late fees vary by bank, so nudges only say what to pay and by when.
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date

UTILISATION_INFO_PCT = 30.0
UTILISATION_WARN_PCT = 70.0


def aed(minor: int) -> str:
    return f"AED {minor / 100:,.2f}"


@dataclass(frozen=True, slots=True)
class CardInput:
    account_id: str
    name: str
    last4: str | None
    credit_limit_minor: int | None
    due_day: int | None
    statement_date: date | None = None
    due_date: date | None = None
    min_due_minor: int | None = None
    total_due_minor: int | None = None
    paid_since_statement_minor: int = 0
    owed_now_minor: int | None = None  # live amount owed (statement + new spend - payments)


@dataclass(slots=True)
class CardDue:
    account_id: str
    name: str
    last4: str | None
    source: str  # statement | account_settings | none
    statement_date: date | None
    due_date: date | None
    days_left: int | None
    minimum_due_minor: int | None
    total_due_minor: int | None
    remaining_minimum_due_minor: int | None
    remaining_total_due_minor: int | None
    paid_since_statement_minor: int
    status: str  # overdue | due_soon | upcoming | paid | unknown
    credit_limit_minor: int | None
    owed_now_minor: int | None
    utilisation_pct: float | None
    available_credit_minor: int | None
    nudges: list[dict[str, str]] = field(default_factory=list)


def next_due_from_day(today: date, due_day: int) -> date:
    for offset in (0, 1):
        idx = today.year * 12 + today.month - 1 + offset
        y, m = idx // 12, idx % 12 + 1
        d = date(y, m, min(due_day, monthrange(y, m)[1]))
        if d >= today:
            return d
    raise AssertionError("unreachable")


def card_due(c: CardInput, today: date) -> CardDue:
    due, source = c.due_date, "statement"
    if due is None and c.due_day:
        due, source = next_due_from_day(today, c.due_day), "account_settings"
    if due is None:
        source = "none"
    has_amounts = c.total_due_minor is not None and c.due_date is not None
    rem_total = (
        max(0, c.total_due_minor - c.paid_since_statement_minor) if c.total_due_minor is not None else None
    )
    rem_min = max(0, c.min_due_minor - c.paid_since_statement_minor) if c.min_due_minor is not None else None
    days_left = (due - today).days if due else None
    if has_amounts and rem_total == 0:
        status = "paid"
    elif days_left is None:
        status = "unknown"
    elif days_left < 0 and has_amounts:
        status = "overdue"
    elif days_left <= 5:
        status = "due_soon"
    else:
        status = "upcoming"
    util = avail = None
    if c.credit_limit_minor and c.owed_now_minor is not None:
        util = round(max(0, c.owed_now_minor) / c.credit_limit_minor * 100, 1)
        avail = c.credit_limit_minor - c.owed_now_minor
    out = CardDue(
        account_id=c.account_id, name=c.name, last4=c.last4, source=source if has_amounts or due else "none",
        statement_date=c.statement_date, due_date=due, days_left=days_left,
        minimum_due_minor=c.min_due_minor, total_due_minor=c.total_due_minor,
        remaining_minimum_due_minor=rem_min, remaining_total_due_minor=rem_total,
        paid_since_statement_minor=c.paid_since_statement_minor, status=status,
        credit_limit_minor=c.credit_limit_minor, owed_now_minor=c.owed_now_minor,
        utilisation_pct=util, available_credit_minor=avail,
    )  # fmt: skip
    out.nudges = nudges(out)
    return out


def nudges(d: CardDue) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    label = d.name + (f" ...{d.last4}" if d.last4 else "")
    if d.status in ("overdue", "due_soon") and d.due_date and d.remaining_total_due_minor is not None:
        mn = aed(d.remaining_minimum_due_minor or 0)
        total = aed(d.remaining_total_due_minor)
        if d.status == "overdue":
            out.append(
                {
                    "level": "urgent",
                    "message": f"{label} was due {d.due_date.isoformat()}: pay at least {mn} now "
                    f"({total} clears the statement).",
                }
            )
        else:
            level = "urgent" if (d.days_left or 0) <= 2 else "warn"
            when = "today" if d.days_left == 0 else f"in {d.days_left} day{'s' if d.days_left != 1 else ''}"
            out.append(
                {
                    "level": level,
                    "message": f"{label} is due {when} ({d.due_date.isoformat()}): minimum {mn}, "
                    f"full statement {total}.",
                }
            )
    elif d.status == "due_soon" and d.due_date:
        out.append(
            {
                "level": "warn",
                "message": f"{label} is due {d.due_date.isoformat()}; add the statement for exact amounts.",
            }
        )
    if d.utilisation_pct is not None:
        if d.utilisation_pct >= UTILISATION_WARN_PCT:
            out.append(
                {"level": "warn", "message": f"{label} is {d.utilisation_pct:.0f}% of its limit used."}
            )
        elif d.utilisation_pct >= UTILISATION_INFO_PCT:
            out.append(
                {
                    "level": "info",
                    "message": f"{label} is {d.utilisation_pct:.0f}% used; keeping cards under "
                    f"{UTILISATION_INFO_PCT:.0f}% of the limit is commonly advised.",
                }
            )
    return out

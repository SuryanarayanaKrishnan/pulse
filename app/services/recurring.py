"""Recurring payment / subscription detection. Pure and deterministic (no database, no network).

A series is a merchant with at least ``MIN_OCCURRENCES`` debits whose gaps fit one cadence (weekly,
fortnightly, monthly, quarterly, yearly). Amounts are *fixed* when every charge is within 5% of the median
(subscriptions, rent) and *variable* otherwise (utilities, phone bills).
"""

from calendar import monthrange
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

MIN_OCCURRENCES = 3
MIN_OCCURRENCES_YEARLY = 2
FIXED_TOLERANCE = 0.05
PRICE_INCREASE_MIN_PCT = 3.0
PRICE_INCREASE_MIN_MINOR = 100  # ignore sub-AED-1 wobble
FORGOTTEN_AFTER_CHARGES = 4  # an unreviewed subscription seen this many times is worth a second look
IGNORED_CATEGORIES = frozenset({"transfer", "cc_payment", "salary", "other_income", "fees", "savings"})

# (name, nominal days, tolerance days)
CADENCES: tuple[tuple[str, int, int], ...] = (
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("monthly", 30, 4),
    ("quarterly", 91, 7),
    ("yearly", 365, 12),
)

# Merchants (lower-case substring of the normalised name) that are subscriptions by nature.
SUBSCRIPTION_MERCHANTS = (
    "netflix", "spotify", "anghami", "shahid", "osn", "disney", "apple.com/bill", "apple music", "icloud",
    "google one", "google play", "youtube", "amazon prime", "prime video", "adobe", "microsoft", "chatgpt",
    "openai", "claude", "notion", "dropbox", "canva", "linkedin", "gym", "fitness", "talabat pro",
    "deliveroo plus", "noon one", "careem plus", "starzplay", "tod", "playstation", "xbox", "nintendo",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Charge:
    posted_on: date
    amount_minor: int
    merchant: str  # display name
    key: str  # normalised grouping key
    category_slug: str | None = None


@dataclass(slots=True)
class Series:
    key: str
    merchant: str
    cadence: str
    interval_days: int
    amount_kind: str  # fixed | variable
    typical_amount_minor: int
    last_amount_minor: int
    last_charge_on: date
    next_expected_on: date
    occurrences: int
    first_charge_on: date
    category_slug: str | None
    is_subscription: bool
    status: str  # active | due | lapsed
    anchor_day: int = 1
    price_increase: dict[str, int | float] | None = None
    flags: list[str] = field(default_factory=list)


def _clamp(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def add_months(d: date, months: int, anchor_day: int | None = None) -> date:
    idx = d.year * 12 + d.month - 1 + months
    return _clamp(idx // 12, idx % 12 + 1, anchor_day or d.day)


def advance(d: date, cadence: str, anchor_day: int | None = None) -> date:
    if cadence == "weekly":
        return d + timedelta(days=7)
    if cadence == "fortnightly":
        return d + timedelta(days=14)
    if cadence == "monthly":
        return add_months(d, 1, anchor_day)
    if cadence == "quarterly":
        return add_months(d, 3, anchor_day)
    return add_months(d, 12, anchor_day)


def _classify(gaps: list[int]) -> tuple[str, int] | None:
    """The cadence at least 70% of gaps fit, judged around the median gap."""
    med = median(gaps)
    for name, nominal, tol in CADENCES:
        if abs(med - nominal) <= tol:
            fit = sum(1 for g in gaps if abs(g - nominal) <= tol * 2) / len(gaps)
            if fit >= 0.7:
                return name, nominal
    return None


def is_subscription_merchant(key: str) -> bool:
    return any(m in key for m in SUBSCRIPTION_MERCHANTS)


def detect(charges: list[Charge], today: date) -> list[Series]:
    """All recurring series in ``charges`` (debits only), newest-next first."""
    groups: dict[str, list[Charge]] = defaultdict(list)
    for c in charges:
        if c.amount_minor > 0 and c.category_slug not in IGNORED_CATEGORIES and c.key:
            groups[c.key].append(c)
    out: list[Series] = []
    for key, items in groups.items():
        items.sort(key=lambda c: c.posted_on)
        # Several charges on one day (split payments) count as one occurrence.
        days: list[Charge] = []
        for c in items:
            if days and days[-1].posted_on == c.posted_on:
                days[-1] = Charge(
                    c.posted_on, days[-1].amount_minor + c.amount_minor, c.merchant, key, c.category_slug
                )
            else:
                days.append(c)
        if len(days) < MIN_OCCURRENCES_YEARLY:
            continue
        gaps = [(b.posted_on - a.posted_on).days for a, b in zip(days, days[1:], strict=False)]
        found = _classify(gaps)
        if found is None:
            continue
        cadence, nominal = found
        if len(days) < MIN_OCCURRENCES and cadence != "yearly":
            continue
        amounts = [c.amount_minor for c in days]
        med = int(median(amounts))
        fixed = all(abs(a - med) <= med * FIXED_TOLERANCE for a in amounts)
        day_counts = Counter(c.posted_on.day for c in days)
        anchor = max(day_counts, key=lambda d: (day_counts[d], d))  # most common day, latest on ties
        last = days[-1]
        nxt = advance(last.posted_on, cadence, anchor)
        grace = max(3, nominal // 6)
        status = "active" if nxt >= today else ("due" if (today - nxt).days <= grace else "lapsed")
        series = Series(
            key=key,
            merchant=last.merchant,
            cadence=cadence,
            interval_days=nominal,
            amount_kind="fixed" if fixed else "variable",
            typical_amount_minor=med if fixed else int(median(amounts[-3:])),
            last_amount_minor=last.amount_minor,
            last_charge_on=last.posted_on,
            next_expected_on=nxt,
            occurrences=len(days),
            first_charge_on=days[0].posted_on,
            category_slug=last.category_slug,
            is_subscription=is_subscription_merchant(key),
            status=status,
            anchor_day=anchor,
        )
        _price_increase(series, amounts)
        out.append(series)
    out.sort(key=lambda s: (s.next_expected_on, s.merchant))
    return out


def _price_increase(s: Series, amounts: list[int]) -> None:
    """Flag when the latest charge is clearly above a previously stable amount."""
    earlier = amounts[:-1]
    if len(earlier) < 2:
        return
    base = int(median(earlier))
    if not all(abs(a - base) <= base * FIXED_TOLERANCE for a in earlier):
        return  # earlier history was not stable, so "increase" would be noise
    new = amounts[-1]
    pct = (new - base) / base * 100 if base else 0.0
    if new - base >= PRICE_INCREASE_MIN_MINOR and pct >= PRICE_INCREASE_MIN_PCT:
        s.price_increase = {
            "from_minor": base,
            "to_minor": new,
            "increase_minor": new - base,
            "pct": round(pct, 1),
        }
        s.flags.append("price_increase")
        s.amount_kind = "fixed"  # stable until this one change
        s.typical_amount_minor = new  # future projections use the new price


def forgotten(s: Series, reviewed: bool) -> bool:
    """A subscription that keeps charging and that the user never told us they want."""
    return (
        s.is_subscription
        and not reviewed
        and s.status != "lapsed"
        and s.occurrences >= FORGOTTEN_AFTER_CHARGES
        and s.cadence in ("monthly", "quarterly", "yearly")
    )


def occurrences_between(s: Series, start: date, end: date) -> list[date]:
    """Expected charge dates of an active/due series in [start, end] (inclusive)."""
    if s.status == "lapsed":
        return []
    anchor = s.anchor_day
    d = s.next_expected_on
    out: list[date] = []
    if d < start:  # due but not yet seen: it is expected as soon as possible
        out.append(start)
        d = advance(d, s.cadence, anchor)
    while d <= end:
        if d >= start:
            out.append(d)
        d = advance(d, s.cadence, anchor)
    return out


def monthly_cost_minor(s: Series) -> int:
    factor = {"weekly": 52 / 12, "fortnightly": 26 / 12, "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}
    return round(s.typical_amount_minor * factor[s.cadence])

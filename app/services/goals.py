"""Savings-goal arithmetic (pure)."""

from datetime import date
from math import ceil


def months_left(today: date, target: date | None) -> int | None:
    """Whole months from today to the target date, at least 1 while the goal is in the future."""
    if target is None or target <= today:
        return None
    months = (target.year - today.year) * 12 + target.month - today.month
    if target.day < today.day:
        months -= 1
    return max(1, months)


def required_monthly(remaining_minor: int, today: date, target: date | None) -> int | None:
    if remaining_minor <= 0:
        return 0
    m = months_left(today, target)
    return None if m is None else ceil(remaining_minor / m)


def monthly_reserve(remaining_minor: int, planned: int | None, today: date, target: date | None) -> int:
    """Monthly amount to set aside: the plan if set, else what the deadline needs, capped at what is left."""
    if remaining_minor <= 0:
        return 0
    amount = planned if planned is not None else (required_monthly(remaining_minor, today, target) or 0)
    return min(amount, remaining_minor)

"""Household privacy rules (pure): who may see how much of whose finances.

Each member sets the level they share with the household; nobody can raise it for them. Viewers see their
own data in full. A dependent sees only their own data. Everyone else sees at most the level the member chose.
"""

from collections import defaultdict
from dataclasses import dataclass

from app.models.household import HouseholdRole, ShareLevel

RANK = {ShareLevel.nothing: 0, ShareLevel.totals: 1, ShareLevel.categories: 2, ShareLevel.full: 3}


def visible_level(viewer_role: HouseholdRole, is_self: bool, shared: ShareLevel) -> ShareLevel:
    if is_self:
        return ShareLevel.full
    if viewer_role is HouseholdRole.dependent:
        return ShareLevel.nothing
    return shared


def at_least(level: ShareLevel, minimum: ShareLevel) -> bool:
    return RANK[level] >= RANK[minimum]


def can_invite(role: HouseholdRole) -> bool:
    return role in (HouseholdRole.owner, HouseholdRole.adult)


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    key: str
    name: str
    slug: str | None
    total_minor: int


def merge_categories(per_member: list[list[CategoryTotal]]) -> list[CategoryTotal]:
    merged: dict[str, CategoryTotal] = {}
    totals: dict[str, int] = defaultdict(int)
    for member in per_member:
        for c in member:
            merged.setdefault(c.key, c)
            totals[c.key] += c.total_minor
    out = [CategoryTotal(k, merged[k].name, merged[k].slug, t) for k, t in totals.items()]
    return sorted(out, key=lambda c: -c.total_minor)


def mask_phone(e164: str | None) -> str | None:
    return f"{e164[:4]} *** **{e164[-2:]}" if e164 and len(e164) >= 8 else None

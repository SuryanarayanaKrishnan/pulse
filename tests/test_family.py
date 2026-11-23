"""Pure household privacy rules."""

from app.models.household import HouseholdRole as R
from app.models.household import ShareLevel as L
from app.services import family


def test_visible_level_rules():
    assert family.visible_level(R.adult, True, L.nothing) == L.full  # you always see yourself
    assert family.visible_level(R.dependent, True, L.nothing) == L.full
    assert family.visible_level(R.dependent, False, L.full) == L.nothing  # dependents see no one else
    assert family.visible_level(R.owner, False, L.totals) == L.totals
    assert family.visible_level(R.adult, False, L.nothing) == L.nothing


def test_at_least_ordering_and_invite_rights():
    assert family.at_least(L.full, L.categories) and family.at_least(L.categories, L.categories)
    assert not family.at_least(L.totals, L.categories) and not family.at_least(L.nothing, L.totals)
    assert family.can_invite(R.owner) and family.can_invite(R.adult) and not family.can_invite(R.dependent)


def test_merge_categories_sums_by_key_and_sorts():
    a = [
        family.CategoryTotal("groceries", "Groceries", "groceries", 300),
        family.CategoryTotal("x", "Mine", None, 50),
    ]
    b = [
        family.CategoryTotal("groceries", "Groceries", "groceries", 200),
        family.CategoryTotal("dining", "Dining", "dining", 400),
    ]
    out = family.merge_categories([a, b])
    assert [(c.key, c.total_minor) for c in out] == [("groceries", 500), ("dining", 400), ("x", 50)]
    assert family.merge_categories([]) == []


def test_mask_phone():
    assert family.mask_phone("+971501234567") == "+971 *** **67"
    assert family.mask_phone(None) is None

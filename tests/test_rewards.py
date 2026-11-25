"""Pure rewards-engine tests (no database)."""

from datetime import date
from decimal import Decimal

import pytest

from app.services import rewards as r

D = Decimal


def spec(**over):
    data = {
        "unit": "cashback",
        "point_value_aed": 1,
        "base_rate": 0.01,
        "rules": [
            {"name": "Dining", "categories": ["dining"], "rate": 0.05, "monthly_cap_units": 100},
            {"name": "Noon", "merchants": ["noon"], "rate": 0.10, "monthly_cap_units": 50},
        ],
        "excluded_categories": ["fees"],
        **over,
    }
    return r.parse_structure(data)


def test_rule_priority_merchant_over_category_over_base_and_exclusions():
    s = spec()
    assert r.select_rule(s, "dining", "noon food").name == "Noon"
    assert r.select_rule(s, "dining", "carrefour").name == "Dining"
    assert r.select_rule(s, "groceries", None).name == r.BASE_RULE
    assert r.select_rule(s, "fees", "noon") is None


def test_cap_binds_chronologically_and_never_goes_negative():
    s = spec()
    res = r.compute_cycle(s, [r.Spend(1_500_000, "dining"), r.Spend(500_000, "dining")])
    assert res.earned_units == D(100)  # 750 + 250 would be 100 -> capped at the first spend already
    assert res.usage["Dining"].remaining_units == 0
    assert res.usage["Dining"].pct_used == D(100)


def test_min_monthly_spend_zeroes_value_until_met():
    s = spec(min_monthly_spend_minor=200_000)
    low = r.compute_cycle(s, [r.Spend(100_000, "dining")])
    assert not low.min_spend_met and low.earned_value_minor(s) == 0 and low.earned_units == D(50)
    ok = r.compute_cycle(s, [r.Spend(250_000, "dining")])
    assert ok.min_spend_met and ok.earned_value_minor(s) == 10_000  # 5% of 2500 = 125, capped at 100


def test_points_value_and_quote_respects_headroom():
    s = spec(unit="points", point_value_aed=0.025, base_rate=1, rules=[])
    assert r.compute_cycle(s, [r.Spend(10_000, None)]).earned_units == D(100)
    assert s.value_minor(D(100)) == 250
    s2 = spec()
    state = r.compute_cycle(s2, [r.Spend(180_000, "dining")])  # 90 of 100 used
    q = r.quote(s2, state, r.Spend(40_000, "dining"))  # would be 20, only 10 left
    assert q.units == D(10) and q.capped and q.headroom_units == D(10) and q.rule == "Dining"
    assert state.earned_units == D(90)  # quote is read-only


def test_non_positive_spend_earns_nothing():
    s = spec()
    assert r.compute_cycle(s, [r.Spend(0, "dining"), r.Spend(-500, "dining")]).earned_units == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": "miles"},
        {"point_value_aed": 0},
        {"base_rate": -1},
        {"rules": [{"name": "x", "rate": 1}]},
    ],
)
def test_garbage_structure_is_rejected(bad):
    with pytest.raises(ValueError):
        r.parse_structure(bad)


@pytest.mark.parametrize(
    ("today", "day", "start", "end"),
    [
        (date(2026, 3, 10), None, date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 3, 10), 15, date(2026, 2, 16), date(2026, 3, 15)),
        (date(2026, 3, 20), 15, date(2026, 3, 16), date(2026, 4, 15)),
        (date(2026, 3, 15), 15, date(2026, 2, 16), date(2026, 3, 15)),
        (date(2026, 1, 5), 20, date(2025, 12, 21), date(2026, 1, 20)),
        (date(2026, 12, 25), 20, date(2026, 12, 21), date(2027, 1, 20)),
        (date(2026, 3, 1), 31, date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 3, 5), 31, date(2026, 3, 1), date(2026, 3, 31)),
    ],
)
def test_cycle_bounds(today, day, start, end):
    assert r.cycle_bounds(today, day) == (start, end)


def test_quote_with_intel_blends_override_only_for_matching_channel():
    from decimal import Decimal

    from app.services import rewards as r

    spec = r.parse_structure(
        {
            "unit": "cashback",
            "base": {"rate": 0.005},
            "rules": [{"name": "Online", "rate": 0.10, "categories": ["shopping"], "cap": 300}],
        }
    )
    state = r.compute_cycle(spec, [])
    adj = [
        {
            "type": "rate_override",
            "card_id": "sib-cashback",
            "when": {"merchant_keys": ["tabby"], "payment_channel": "card_details"},
            "rate": 0.005,
            "trust": 0.5,
        }
    ]
    base = r.quote(spec, state, r.Spend(100_000, "shopping", "tabby")).units
    wallet = r.quote_with_intel(
        spec, state, r.Spend(100_000, "shopping", "tabby", "wallet"), "sib-cashback", adj
    )
    unknown = r.quote_with_intel(spec, state, r.Spend(100_000, "shopping", "tabby"), "sib-cashback", adj)
    card = r.quote_with_intel(
        spec, state, r.Spend(100_000, "shopping", "tabby", "card_details"), "sib-cashback", adj
    )
    other = r.quote_with_intel(
        spec, state, r.Spend(100_000, "shopping", "tabby", "card_details"), "other-card", adj
    )
    assert wallet.units == unknown.units == other.units == base
    # trust 0.5: halfway between 10% and 0.5% -> 5.25%
    assert card.units == base * Decimal("0.0525") / Decimal("0.10")

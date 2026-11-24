"""Pure recurring-detection and goal-arithmetic tests."""

from datetime import date, timedelta

from app.services import goals
from app.services import recurring as rc

TODAY = date(2026, 6, 15)


def monthly(key, amounts, last=date(2026, 6, 5), slug=None, merchant=None):
    out, d = [], last
    for a in reversed(amounts):
        out.append(rc.Charge(d, a, merchant or key.title(), key, slug))
        d = rc.add_months(d, -1, last.day)
    return out[::-1]


def one(charges, today=TODAY):
    found = rc.detect(charges, today)
    assert len(found) == 1, found
    return found[0]


def test_steady_monthly_subscription_is_detected_and_projected():
    s = one(monthly("netflix", [5_500] * 5))
    assert s.cadence == "monthly" and s.amount_kind == "fixed" and s.status == "active"
    assert s.next_expected_on == date(2026, 7, 5) and s.is_subscription
    assert rc.occurrences_between(s, TODAY, date(2026, 9, 10)) == [
        date(2026, 7, 5), date(2026, 8, 5), date(2026, 9, 5)
    ]  # fmt: skip
    assert rc.monthly_cost_minor(s) == 5_500


def test_two_charges_are_not_enough_unless_yearly():
    assert rc.detect(monthly("gym", [10_000] * 2), TODAY) == []
    yearly = [
        rc.Charge(date(2024, 7, 1), 36_500, "Icloud", "icloud"),
        rc.Charge(date(2025, 7, 1), 36_500, "Icloud", "icloud"),
    ]
    s = one(yearly)
    assert s.cadence == "yearly" and s.next_expected_on == date(2026, 7, 1)


def test_irregular_spend_is_not_recurring():
    d, charges = date(2026, 1, 1), []
    for gap in (3, 40, 9, 61):
        d += timedelta(days=gap)
        charges.append(rc.Charge(d, 4_000, "Cafe", "cafe"))
    assert rc.detect(charges, TODAY) == []


def test_price_increase_flagged_only_after_stable_history():
    s = one(monthly("spotify", [2_199, 2_199, 2_199, 2_199, 2_699]))
    assert s.price_increase == {"from_minor": 2_199, "to_minor": 2_699, "increase_minor": 500, "pct": 22.7}
    assert "price_increase" in s.flags and s.typical_amount_minor == 2_699
    # an erratic history is a variable bill, not a price rise
    assert one(monthly("dewa", [30_000, 41_000, 25_000, 38_000, 52_000])).price_increase is None
    # tiny wobble below AED 1 is ignored
    assert one(monthly("shahid", [2_999, 2_999, 2_999, 3_050])).price_increase is None


def test_variable_bills_use_recent_median():
    s = one(monthly("dewa", [30_000, 41_000, 25_000, 38_000, 52_000]))
    assert s.amount_kind == "variable" and s.typical_amount_minor == 38_000


def test_lapsed_and_due_status():
    s = one(monthly("netflix", [5_500] * 4, last=date(2026, 2, 5)))
    assert s.status == "lapsed" and rc.occurrences_between(s, TODAY, date(2026, 12, 31)) == []
    due = one(monthly("netflix", [5_500] * 4, last=date(2026, 5, 13)))  # expected 13 Jun, 2 days ago
    assert due.status == "due" and rc.occurrences_between(due, TODAY, date(2026, 6, 20))[0] == TODAY


def test_forgotten_requires_subscription_history_and_no_review():
    s = one(monthly("netflix", [5_500] * 5))
    assert rc.forgotten(s, reviewed=False) and not rc.forgotten(s, reviewed=True)
    assert not rc.forgotten(one(monthly("netflix", [5_500] * 3)), reviewed=False)
    assert not rc.forgotten(one(monthly("dewa", [30_000] * 6)), reviewed=False)  # a utility


def test_ignored_categories_and_same_day_splits():
    assert rc.detect(monthly("savings pot", [50_000] * 5, slug="savings"), TODAY) == []
    split = monthly("rent", [100_000] * 4)
    split.append(rc.Charge(split[-1].posted_on, 20_000, "Rent", "rent"))
    s = one(split)
    assert s.occurrences == 4 and s.last_amount_minor == 120_000


def test_month_end_anchor_survives_short_months():
    s = one(monthly("rent", [500_000] * 4, last=date(2026, 5, 31)), today=date(2026, 6, 10))
    assert s.next_expected_on == date(2026, 6, 30)
    assert rc.occurrences_between(s, date(2026, 6, 1), date(2026, 8, 31)) == [
        date(2026, 6, 30), date(2026, 7, 31), date(2026, 8, 31)
    ]  # fmt: skip


def test_goal_arithmetic():
    today = date(2026, 6, 15)
    assert goals.months_left(today, date(2026, 12, 15)) == 6
    assert goals.months_left(today, date(2026, 12, 14)) == 5
    assert goals.months_left(today, date(2026, 6, 20)) == 1
    assert goals.months_left(today, date(2026, 6, 1)) is None and goals.months_left(today, None) is None
    assert goals.required_monthly(100_000, today, date(2026, 12, 15)) == 16_667
    assert goals.required_monthly(0, today, None) == 0
    assert goals.monthly_reserve(100_000, 30_000, today, None) == 30_000
    assert goals.monthly_reserve(10_000, 30_000, today, None) == 10_000
    assert goals.monthly_reserve(100_000, None, today, None) == 0

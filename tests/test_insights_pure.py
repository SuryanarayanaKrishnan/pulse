"""Pure tests: card dues, cashflow, projection, safe-to-spend and the health score."""

from datetime import date

from app.services import cashflow as cf
from app.services import dues, health
from app.services import safe_to_spend as sts

TODAY = date(2026, 6, 15)


def card(**over):
    base = {
        "account_id": "a", "name": "ENBD Visa", "last4": "1234", "credit_limit_minor": 1_000_000,
        "due_day": None, "statement_date": date(2026, 6, 1), "due_date": date(2026, 6, 25),
        "min_due_minor": 10_000, "total_due_minor": 400_000, "paid_since_statement_minor": 0,
        "owed_now_minor": 450_000,
    }  # fmt: skip
    return dues.card_due(dues.CardInput(**{**base, **over}), TODAY)


def test_minimum_and_total_due_are_separate_and_shrink_with_payments():
    d = card()
    assert (d.minimum_due_minor, d.total_due_minor, d.days_left, d.status) == (
        10_000,
        400_000,
        10,
        "upcoming",
    )
    part = card(paid_since_statement_minor=60_000)
    assert part.remaining_minimum_due_minor == 0 and part.remaining_total_due_minor == 340_000
    assert card(paid_since_statement_minor=400_000).status == "paid"


def test_status_transitions_and_nudges():
    soon = card(due_date=date(2026, 6, 17))
    assert soon.status == "due_soon" and soon.days_left == 2
    assert soon.nudges[0]["level"] == "urgent" and "AED 100.00" in soon.nudges[0]["message"]
    assert "AED 4,000.00" in soon.nudges[0]["message"]
    late = card(due_date=date(2026, 6, 10))
    assert late.status == "overdue" and late.days_left == -5 and late.nudges[0]["level"] == "urgent"
    today_due = card(due_date=TODAY)
    assert today_due.days_left == 0 and "today" in today_due.nudges[0]["message"]


def test_utilisation_nudges_and_missing_statement_fallback():
    assert card(owed_now_minor=200_000).nudges == []
    assert card(owed_now_minor=350_000).nudges[-1]["level"] == "info"
    heavy = card(owed_now_minor=900_000)
    assert heavy.utilisation_pct == 90.0 and heavy.nudges[-1]["level"] == "warn"
    assert heavy.available_credit_minor == 100_000
    nostmt = card(statement_date=None, due_date=None, min_due_minor=None, total_due_minor=None, due_day=20)
    assert (
        nostmt.source == "account_settings" and nostmt.due_date == date(2026, 6, 20) and nostmt.days_left == 5
    )
    assert nostmt.minimum_due_minor is None and nostmt.status == "due_soon"
    assert "add the statement" in nostmt.nudges[0]["message"]
    assert (
        card(statement_date=None, due_date=None, total_due_minor=None, min_due_minor=None).status == "unknown"
    )


def test_due_day_rolls_to_next_month_and_clamps():
    assert dues.next_due_from_day(date(2026, 6, 15), 10) == date(2026, 7, 10)
    assert dues.next_due_from_day(date(2026, 1, 31), 31) == date(2026, 1, 31)
    assert dues.next_due_from_day(date(2026, 2, 5), 31) == date(2026, 2, 28)


def flows():
    f = lambda d, amt, credit, slug=None, ex=False: cf.Flow(d, amt, credit, slug, ex)  # noqa: E731
    return [
        f(date(2026, 5, 1), 1_000_000, True, "salary"),
        f(date(2026, 5, 3), 300_000, False, "groceries"),
        f(date(2026, 5, 4), 100_000, False, "savings"),
        f(date(2026, 5, 5), 500_000, False, "cc_payment"),
        f(date(2026, 5, 6), 50_000, True, "transfer"),
        f(date(2026, 5, 7), 20_000, False, "shopping", ex=True),
        f(date(2026, 6, 1), 1_000_000, True, "salary"),
        f(date(2026, 6, 2), 1_200_000, False, "rent"),
    ]


def test_monthly_cashflow_excludes_transfers_card_payments_and_savings():
    months = cf.monthly_cashflow(flows(), TODAY, 3)
    assert [m.month for m in months] == [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]
    may = months[1]
    assert (may.income_minor, may.spend_minor, may.saved_minor, may.net_minor) == (
        1_000_000,
        300_000,
        100_000,
        700_000,
    )
    assert may.savings_rate == 0.7
    assert months[0].savings_rate is None  # no income
    assert months[2].net_minor == -200_000 and months[2].savings_rate == -0.2


def test_months_back_crosses_years():
    assert cf.months_back(date(2026, 2, 10), 4) == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


def test_projection_applies_daily_spend_then_events():
    ev = [
        cf.Event(date(2026, 6, 17), "Netflix", -5_000, "bill"),
        cf.Event(date(2026, 6, 20), "Salary (expected)", 1_000_000, "salary"),
    ]
    path = cf.project_balance(100_000, TODAY, 6, ev, 10_000)
    assert path[0].balance_minor == 100_000 and len(path) == 7
    assert path[2].balance_minor == 100_000 - 20_000 - 5_000 and path[2].events[0].label == "Netflix"
    assert path[5].balance_minor == 100_000 - 50_000 - 5_000 + 1_000_000


def test_safe_to_spend_breakdown_and_unknown_balance():
    items = [
        sts.Commitment("bill", "DEWA", 40_000, date(2026, 6, 20)),
        sts.Commitment("card_due", "ENBD Visa", 400_000, date(2026, 6, 25)),
        sts.Commitment("goal", "Holiday", 50_000, None),
    ]
    r = sts.compute(1_000_000, items, TODAY, date(2026, 6, 30))
    assert r.committed_minor == 490_000 and r.safe_minor == 510_000 and r.status == "ok"
    assert r.per_day_minor == 510_000 // 15 and r.by_kind == {
        "bill": 40_000,
        "card_due": 400_000,
        "goal": 50_000,
    }
    short = sts.compute(300_000, items, TODAY, date(2026, 6, 30))
    assert short.safe_minor == -190_000 and short.status == "shortfall" and short.per_day_minor == 0
    unknown = sts.compute(None, items, TODAY, date(2026, 6, 30))
    assert unknown.safe_minor is None and unknown.status == "unknown" and unknown.committed_minor == 490_000


def test_health_components_scoring():
    assert health.savings_component(0.2).score == 100 and health.savings_component(0.1).score == 50
    assert health.savings_component(-0.3).score == 0 and health.savings_component(None).score is None
    util = lambda pct: health.utilisation_component(pct * 100, 10_000).score  # noqa: E731
    assert [util(p) for p in (0, 10, 30, 50, 80, 100)] == [100, 100, 70, 30, 0, 0]
    assert health.utilisation_component(None, 10_000).score is None
    assert health.on_time_component(5, 6).score == 83 and health.on_time_component(0, 0).score is None
    assert (
        health.runway_component(600_000, 100_000).score == 100
        and health.runway_component(300_000, 100_000).score == 50
    )
    assert health.runway_component(None, 100_000).score is None


def test_health_overall_renormalises_and_refuses_with_thin_data():
    full = health.compute(
        [
            health.savings_component(0.2),
            health.utilisation_component(0, 10_000),
            health.on_time_component(6, 6),
            health.runway_component(300_000, 100_000),
        ]
    )
    assert full.score == round((100 * 30 + 100 * 25 + 100 * 25 + 50 * 20) / 100) == 90
    assert full.band == "strong" and full.coverage == 1.0 and "emergency cushion" in full.summary
    partial = health.compute(
        [
            health.savings_component(0.1),
            health.utilisation_component(None, None),
            health.on_time_component(0, 0),
            health.runway_component(None, None),
        ]
    )
    assert partial.score is None and partial.coverage == 0.3 and "Not enough data" in partial.summary
    half = health.compute(
        [
            health.savings_component(0.2),
            health.utilisation_component(0, 10_000),
            health.on_time_component(0, 0),
            health.runway_component(None, None),
        ]
    )
    assert half.score == 100 and half.coverage == 0.55

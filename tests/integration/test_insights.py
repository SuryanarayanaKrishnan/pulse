"""Balances, goals, recurring detection, cashflow, due dates, safe-to-spend, health score, net-worth history."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from app.models.statement import ParseStatus, Statement
from app.services.insights_service import today_dubai
from app.services.recurring import add_months
from app.workers.tasks import snapshot_net_worth
from tests.integration.test_flows import mk_account

TODAY = today_dubai()


def at(d: date, hour: int = 9) -> str:
    if d == TODAY:  # "today" means just now, i.e. after any balance entered a moment ago
        return datetime.now(UTC).isoformat()
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC).isoformat()


async def txn(api, account_id, amount, direction, d, merchant=None, raw=None, category_id=None):
    body = {
        "account_id": account_id, "amount_minor": amount, "direction": direction, "posted_at": at(d),
        "merchant_normalized": merchant, "merchant_raw": raw or merchant,
    }  # fmt: skip
    if category_id:
        body["category_id"] = category_id
    r = await api.post("/v1/transactions", body)
    assert r.status_code == 201, r.text
    return r.json()


async def cat_id(api, name):
    return next(c["id"] for c in (await api.get("/v1/categories")).json() if c["name"] == name)


def months_ago(n: int, day: int = 10) -> date:
    return add_months(TODAY.replace(day=1), -n).replace(day=day)


async def add_statement(sessionmaker_, user_id, account_id, *, statement_date, due_date, min_due, total):
    async with sessionmaker_() as s:
        s.add(
            Statement(
                user_id=user_id, account_id=account_id, kind="credit_card", institution="enbd",
                period_start=statement_date - timedelta(days=30), period_end=statement_date,
                closing_balance_minor=total, parse_status=ParseStatus.parsed,
                summary={
                    "statement_date": statement_date.isoformat(), "due_date": due_date.isoformat(),
                    "min_due_minor": min_due,
                },
            )
        )  # fmt: skip
        await s.commit()


async def test_account_balance_anchor_moves_with_transactions(new_user):
    api = new_user()
    acc = await mk_account(api, balance_minor=1_000_000)
    assert acc["balance_anchor_minor"] == 1_000_000 and acc["balance_anchor_at"]
    unknown = await mk_account(api, name="Savings", last4="9999")
    assert unknown["balance_anchor_minor"] is None
    await txn(api, acc["id"], 200_000, "debit", TODAY, "Shop")
    await txn(api, acc["id"], 50_000, "credit", TODAY, "Refund")
    s = (await api.get("/v1/insights/safe-to-spend")).json()
    assert s["liquid_minor"] == 850_000  # the unknown account is left out and called out
    assert any("without a known balance" in n for n in s["notes"])
    # re-anchor
    up = await api.patch(f"/v1/accounts/{acc['id']}", {"balance_minor": 400_000})
    assert up.json()["balance_anchor_minor"] == 400_000
    assert (await api.get("/v1/insights/safe-to-spend")).json()["liquid_minor"] == 400_000


async def test_goals_crud_computed_fields_and_isolation(new_user):
    a, b = new_user(), new_user()
    target = add_months(TODAY, 6)
    g = (
        await a.post("/v1/goals", {"name": "Holiday", "target_minor": 1_200_000, "saved_minor": 0, "target_date": target.isoformat()})
    ).json()  # fmt: skip
    assert g["months_left"] in (5, 6) and g["required_monthly_minor"] == -(-1_200_000 // g["months_left"])
    assert g["monthly_reserve_minor"] == g["required_monthly_minor"] and g["progress_pct"] == 0
    up = (
        await a.patch(f"/v1/goals/{g['id']}", {"saved_minor": 600_000, "monthly_contribution_minor": 100_000})
    ).json()
    assert (
        up["progress_pct"] == 50
        and up["monthly_reserve_minor"] == 100_000
        and up["remaining_minor"] == 600_000
    )
    done = (await a.patch(f"/v1/goals/{g['id']}", {"saved_minor": 1_200_000})).json()
    assert done["achieved"] and done["monthly_reserve_minor"] == 0
    assert (await a.patch(f"/v1/goals/{g['id']}", {"name": None, "target_date": None})).json()[
        "target_date"
    ] is None
    assert (await b.get(f"/v1/goals/{g['id']}")).status_code == 404
    assert (await a.post("/v1/goals", {"name": "x", "target_minor": 0})).status_code == 422
    assert (await a.delete(f"/v1/goals/{g['id']}")).status_code == 204
    assert (await a.get("/v1/goals")).json() == []


async def test_recurring_detection_flags_and_reviews(new_user):
    api = new_user()
    acc = await mk_account(api, balance_minor=5_000_000)
    last = TODAY - timedelta(days=10)
    for k in range(5):
        d = add_months(last, -k, last.day)
        await txn(api, acc["id"], 6_500 if k == 0 else 5_500, "debit", d, "Netflix")
    for d in (3, 40, 90):  # irregular one-offs never become a series
        await txn(api, acc["id"], 9_900, "debit", TODAY - timedelta(days=d), "Random Cafe")
    r = (await api.get("/v1/recurring")).json()
    assert [s["merchant"] for s in r["series"]] == ["Netflix"]
    nf = r["series"][0]
    assert nf["cadence"] == "monthly" and nf["amount_kind"] == "fixed" and nf["occurrences"] == 5
    assert nf["price_increase"] == {
        "from_minor": 5_500,
        "to_minor": 6_500,
        "increase_minor": 1_000,
        "pct": 18.2,
    }
    assert set(nf["flags"]) == {"price_increase", "forgotten_subscription"}
    assert r["monthly_total_minor"] == 6_500 and r["subscriptions_monthly_minor"] == 6_500
    assert len(r["price_increases"]) == 1 and len(r["forgotten_subscriptions"]) == 1
    up = (await api.get("/v1/recurring/upcoming?days=60")).json()
    assert up["items"] and all(i["name"] == "Netflix" and i["amount_minor"] == 6_500 for i in up["items"])
    assert up["total_minor"] == 6_500 * len(up["items"])
    # keep it: no longer "forgotten"; dismiss it: gone from everything
    assert (await api.put(f"/v1/recurring/{nf['key']}/review", {"status": "confirmed"})).status_code == 204
    kept = (await api.get("/v1/recurring")).json()
    assert kept["forgotten_subscriptions"] == [] and kept["series"][0]["review"] == "confirmed"
    assert (await api.put(f"/v1/recurring/{nf['key']}/review", {"status": "dismissed"})).status_code == 204
    assert (await api.get("/v1/recurring")).json()["series"] == []
    assert (await api.get("/v1/recurring/upcoming?days=60")).json()["items"] == []
    assert len((await api.get("/v1/recurring?include_dismissed=true")).json()["series"]) == 1
    assert (await api.delete(f"/v1/recurring/{nf['key']}/review")).status_code == 204
    assert len((await api.get("/v1/recurring")).json()["series"]) == 1
    assert (await api.put(f"/v1/recurring/{nf['key']}/review", {"status": "bogus"})).status_code == 422


async def test_loan_emi_replaces_the_matching_recurring_series(new_user):
    api = new_user()
    acc = await mk_account(api, balance_minor=5_000_000)
    last = TODAY - timedelta(days=5)
    for k in range(4):
        await txn(api, acc["id"], 150_000, "debit", add_months(last, -k, last.day), "Car Loan EMI")
    liab = await api.post(
        "/v1/liabilities",
        {"kind": "auto_loan", "name": "Car loan", "outstanding_minor": 5_000_000, "emi_minor": 150_000, "next_due_on": add_months(last, 1, last.day).isoformat()},
    )  # fmt: skip
    assert liab.status_code == 201, liab.text
    items = (await api.get("/v1/recurring/upcoming?days=45")).json()["items"]
    assert items and all(
        i["kind"] == "loan" and i["name"] == "Car loan" for i in items
    )  # never counted twice


async def test_cashflow_months_and_projection(new_user):
    api = new_user()
    acc = await mk_account(api, balance_minor=2_000_000)
    groceries, savings = await cat_id(api, "Groceries"), await cat_id(api, "Savings & Investments")
    for n in (1, 2):
        await txn(api, acc["id"], 1_000_000, "credit", months_ago(n, 1), "Acme Payroll")
        await txn(
            api, acc["id"], 100_000 * (n + 1), "debit", months_ago(n, 5), "Carrefour", category_id=groceries
        )
        await txn(api, acc["id"], 50_000, "debit", months_ago(n, 6), "Pot", category_id=savings)
    out = (await api.get("/v1/insights/cashflow?months=12&projection_days=14")).json()
    assert len(out["months"]) == 12 and out["months"][-1]["month"] == TODAY.replace(day=1).isoformat()
    prev = out["months"][-2]
    assert (
        prev["income_minor"] == 1_000_000 and prev["spend_minor"] == 200_000 and prev["saved_minor"] == 50_000
    )
    assert prev["net_minor"] == 800_000 and prev["savings_rate"] == 0.8
    assert out["months"][-3]["spend_minor"] == 300_000
    p = out["projection"]
    assert p["available"] and p["start_balance_minor"] == 2_000_000 and len(p["days"]) == 15
    assert p["days"][0]["date"] == TODAY.isoformat() and p["lowest_balance_minor"] <= 2_000_000
    assert p["daily_variable_spend_minor"] > 0 and p["assumptions"]
    trends = (await api.get("/v1/insights/spend-trends?months=6&top=5")).json()
    g = next(c for c in trends["categories"] if c["slug"] == "groceries")
    assert g["total_minor"] == 500_000 and g["change_pct"] == -33.3  # 200k last month vs 300k before
    assert all(c["slug"] != "savings" for c in trends["categories"])
    assert trends["top_merchants"][0] == {
        "merchant": "Carrefour",
        "total_minor": 500_000,
        "count": 2,
        "last_on": months_ago(1, 5).isoformat(),
    }
    assert len(trends["months"]) == 6


async def test_cashflow_projection_unavailable_without_balances(new_user):
    api = new_user()
    await mk_account(api)
    p = (await api.get("/v1/insights/cashflow")).json()["projection"]
    assert p["available"] is False and "balance" in p["reason"] and p["days"] == []


async def test_card_due_dates_and_safe_to_spend(new_user, sessionmaker_):
    api = new_user()
    await api.get("/v1/me")
    bank = await mk_account(api, balance_minor=1_000_000)
    card = await mk_account(
        api,
        type="credit_card",
        name="Visa",
        last4="1111",
        credit_limit_minor=1_000_000,
        balance_minor=250_000,
        statement_day=1,
        due_day=TODAY.day,
    )
    await add_statement(sessionmaker_, api.user_id, UUID(card["id"]), statement_date=TODAY - timedelta(days=10), due_date=TODAY, min_due=10_000, total=200_000)  # fmt: skip
    await txn(api, card["id"], 30_000, "credit", TODAY, "Card payment")
    due = (await api.get("/v1/cards/due-dates")).json()["cards"]
    assert len(due) == 1
    d = due[0]
    assert d["source"] == "statement" and d["days_left"] == 0 and d["status"] == "due_soon"
    assert (d["minimum_due_minor"], d["total_due_minor"]) == (10_000, 200_000)  # kept apart
    assert d["paid_since_statement_minor"] == 30_000
    assert (d["remaining_minimum_due_minor"], d["remaining_total_due_minor"]) == (0, 170_000)
    assert (
        d["owed_now_minor"] == 220_000
        and d["utilisation_pct"] == 22.0
        and d["available_credit_minor"] == 780_000
    )
    assert "today" in d["nudges"][0]["message"]
    # safe to spend: bank balance minus the card's remaining statement, a loan EMI due today and a goal
    await api.post(
        "/v1/liabilities",
        {"kind": "personal_loan", "name": "Loan", "outstanding_minor": 900_000, "emi_minor": 100_000, "next_due_on": TODAY.isoformat()},
    )  # fmt: skip
    await api.post(
        "/v1/goals", {"name": "Fund", "target_minor": 5_000_000, "monthly_contribution_minor": 60_000}
    )
    s = (await api.get("/v1/insights/safe-to-spend")).json()
    assert s["liquid_minor"] == 1_000_000 and s["status"] == "ok" and s["until_basis"] == "month_end"
    kinds = {c["kind"]: c for c in s["breakdown"]}
    assert kinds["card_due"]["amount_minor"] == 170_000 and kinds["loan"]["amount_minor"] == 100_000
    goal_part = -(-60_000 * min(s["days_left"], 30) // 30)
    assert kinds["goal"]["amount_minor"] == goal_part
    assert s["committed_minor"] == 170_000 + 100_000 + goal_part
    assert s["safe_to_spend_minor"] == 1_000_000 - s["committed_minor"]
    assert s["per_day_minor"] == s["safe_to_spend_minor"] // s["days_left"]
    assert s["by_kind"]["card_due"] == 170_000
    # pay the statement in full: the card_due commitment disappears
    await txn(api, card["id"], 170_000, "credit", TODAY, "Card payment 2")
    again = (await api.get("/v1/insights/safe-to-spend")).json()
    assert all(c["kind"] != "card_due" for c in again["breakdown"])
    assert (await api.get("/v1/cards/due-dates")).json()["cards"][0]["status"] == "paid"
    assert bank["id"]


async def test_safe_to_spend_unknown_without_balances(new_user):
    api = new_user()
    await mk_account(api)
    s = (await api.get("/v1/insights/safe-to-spend")).json()
    assert s["status"] == "unknown" and s["safe_to_spend_minor"] is None and s["liquid_minor"] is None


async def test_health_score(new_user, sessionmaker_):
    api = new_user()
    await api.get("/v1/me")
    empty = (await api.get("/v1/insights/health-score")).json()
    assert empty["score"] is None and "Not enough data" in empty["summary"] and len(empty["components"]) == 4
    acc = await mk_account(api, balance_minor=1_500_000)
    card = await mk_account(
        api,
        type="credit_card",
        name="Visa",
        last4="2222",
        credit_limit_minor=1_000_000,
        balance_minor=100_000,
    )
    groceries = await cat_id(api, "Groceries")
    for n in (1, 2, 3):
        await txn(api, acc["id"], 1_000_000, "credit", months_ago(n, 1), "Acme Payroll")
        await txn(api, acc["id"], 500_000, "debit", months_ago(n, 5), "Carrefour", category_id=groceries)
    # an old statement paid on time (minimum paid before the due date)
    st_date = months_ago(2, 1)
    await add_statement(sessionmaker_, api.user_id, UUID(card["id"]), statement_date=st_date, due_date=st_date + timedelta(days=20), min_due=10_000, total=100_000)  # fmt: skip
    await txn(api, card["id"], 100_000, "credit", st_date + timedelta(days=10), "Payment")
    h = (await api.get("/v1/insights/health-score")).json()
    comps = {c["key"]: c for c in h["components"]}
    assert comps["savings_rate"]["value"] == 0.5 and comps["savings_rate"]["score"] == 100
    assert comps["credit_utilisation"]["value"] == 10.0 and comps["credit_utilisation"]["score"] == 100
    assert comps["on_time_payments"]["score"] == 100
    assert comps["emergency_runway"]["value"] == 3.0 and comps["emergency_runway"]["score"] == 50
    assert h["coverage"] == 1.0 and h["score"] == round((100 * 30 + 100 * 25 + 100 * 25 + 50 * 20) / 100)
    assert h["band"] == "strong" and "Health score 90" in h["summary"]


async def test_net_worth_history_snapshots_and_cron(new_user, sessionmaker_):
    api = new_user()
    await mk_account(api, balance_minor=500_000)
    await mk_account(api, type="credit_card", name="Visa", last4="3333", balance_minor=120_000)
    await api.post("/v1/assets", {"kind": "gold", "name": "Gold", "manual_value_minor": 300_000})
    h = (await api.get("/v1/insights/net-worth-history")).json()
    assert len(h["points"]) == 1 and h["change_minor"] is None
    p = h["points"][0]
    assert (p["assets_minor"], p["liabilities_minor"], p["net_worth_minor"]) == (800_000, 120_000, 680_000)
    await api.post("/v1/assets", {"kind": "cash", "name": "Safe", "manual_value_minor": 100_000})
    again = (await api.get("/v1/insights/net-worth-history")).json()
    assert len(again["points"]) == 1 and again["points"][0]["net_worth_minor"] == 780_000  # same day: updated
    out = await snapshot_net_worth({"session_factory": sessionmaker_})
    assert out["failed"] == 0 and out["snapshotted"] >= 1

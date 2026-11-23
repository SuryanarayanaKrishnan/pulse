"""Default category set (migration 0003), custom categories/sub-categories, budgets with rollover."""

import pytest

from tests.integration.test_flows import mk_account

EXPECTED_DEFAULTS = {
    "Salary", "Other Income", "Groceries", "Dining & Delivery", "Transport & Fuel", "Car Loan",
    "Home Loan / Mortgage", "Rent & Housing", "Utilities", "Telecom & Internet", "Fixed Expenses",
    "Education Fees", "Health", "Shopping", "Travel", "Entertainment", "Family & Remittances",
    "Savings & Investments", "Credit Card Payments", "Transfer", "Fees & Charges", "Other",
}  # fmt: skip


async def cat(api, name: str) -> dict:
    return next(c for c in (await api.get("/v1/categories?include_hidden=true")).json() if c["name"] == name)


async def spend(api, account_id: str, category_id: str, amount: int, day: str):
    r = await api.post(
        "/v1/transactions",
        {
            "account_id": account_id,
            "amount_minor": amount,
            "direction": "debit",
            "category_id": category_id,
            "posted_at": f"{day}T10:00:00+00:00",
        },
    )
    assert r.status_code == 201, r.text


async def test_default_set_is_seeded_and_old_names_are_gone(new_user):
    api = new_user()
    cats = (await api.get("/v1/categories")).json()
    defaults = [c for c in cats if c["is_default"]]
    assert {c["name"] for c in defaults} == EXPECTED_DEFAULTS
    for old in ("Dining", "Transport", "Fuel", "Rent", "Education", "Subscriptions", "Credit Card Payment"):
        assert all(c["name"] != old for c in cats)
    by_name = {c["name"]: c for c in defaults}
    assert by_name["Salary"]["kind"] == "income" and by_name["Salary"]["is_fixed"] is True
    assert by_name["Credit Card Payments"]["kind"] == "transfer"
    assert by_name["Groceries"]["is_fixed"] is False
    assert by_name["Car Loan"]["is_fixed"] is True
    assert all(c["icon"] and c["color"] and c["slug"] for c in defaults)
    assert len({c["slug"] for c in defaults}) == len(defaults)
    assert [c["sort_order"] for c in defaults] == sorted(c["sort_order"] for c in defaults)


async def test_custom_categories_and_one_level_subcategories(new_user):
    a, b = new_user(), new_user()
    groceries = await cat(a, "Groceries")
    r = await a.post("/v1/categories", {"name": "Pets", "icon": "paw", "color": "amber", "is_fixed": False})
    assert r.status_code == 201 and r.json()["kind"] == "expense" and r.json()["is_default"] is False
    pets = r.json()
    # sub-category under a default and under a custom one; kind is inherited
    sub = await a.post("/v1/categories", {"name": "Organic", "parent_id": groceries["id"]})
    assert sub.status_code == 201 and sub.json()["parent_id"] == groceries["id"]
    vet = await a.post("/v1/categories", {"name": "Vet", "parent_id": pets["id"]})
    assert vet.status_code == 201
    # a third level is rejected
    deep = await a.post("/v1/categories", {"name": "Deeper", "parent_id": vet.json()["id"]})
    assert deep.status_code == 422 and deep.json()["error"]["code"] == "category_depth_exceeded"
    mismatch = await a.post(
        "/v1/categories", {"name": "Bonus", "parent_id": groceries["id"], "kind": "income"}
    )
    assert mismatch.status_code == 422
    # names are unique, also against defaults
    assert (await a.post("/v1/categories", {"name": "pets"})).status_code == 409
    assert (await a.post("/v1/categories", {"name": "groceries"})).status_code == 409
    # tenant isolation: b neither sees nor can use a's categories
    assert all(c["name"] != "Pets" for c in (await b.get("/v1/categories")).json())
    assert (await b.post("/v1/categories", {"name": "X", "parent_id": pets["id"]})).status_code == 422
    assert (await b.patch(f"/v1/categories/{pets['id']}", {"name": "Hacked"})).status_code == 404
    assert (await b.delete(f"/v1/categories/{pets['id']}")).status_code == 404
    # b can create the same name independently
    assert (await b.post("/v1/categories", {"name": "Pets"})).status_code == 201
    # edit own
    up = await a.patch(
        f"/v1/categories/{pets['id']}", {"name": "Pet Care", "color": "teal", "is_fixed": True}
    )
    assert up.status_code == 200 and (up.json()["name"], up.json()["color"], up.json()["is_fixed"]) == (
        "Pet Care", "teal", True,
    )  # fmt: skip


async def test_rename_and_hide_defaults_per_user(new_user):
    a, b = new_user(), new_user()
    dining = await cat(a, "Dining & Delivery")
    r = await a.patch(f"/v1/categories/{dining['id']}", {"name": "Eating out"})
    assert r.status_code == 200 and r.json()["name"] == "Eating out"
    assert r.json()["default_name"] == "Dining & Delivery" and r.json()["is_default"] is True
    assert (await cat(b, "Dining & Delivery"))["name"] == "Dining & Delivery"  # b unaffected
    # other fields of a default are locked
    locked = await a.patch(f"/v1/categories/{dining['id']}", {"color": "red"})
    assert locked.status_code == 422 and locked.json()["error"]["code"] == "default_category_locked"
    # renaming onto an existing name conflicts
    assert (await a.patch(f"/v1/categories/{dining['id']}", {"name": "Health"})).status_code == 409
    # reset with null
    reset = await a.patch(f"/v1/categories/{dining['id']}", {"name": None})
    assert reset.json()["name"] == "Dining & Delivery"
    # hide
    health = await cat(a, "Health")
    assert (await a.patch(f"/v1/categories/{health['id']}", {"is_hidden": True})).json()["is_hidden"] is True
    assert all(c["name"] != "Health" for c in (await a.get("/v1/categories")).json())
    assert any(c["name"] == "Health" for c in (await a.get("/v1/categories?include_hidden=true")).json())
    assert any(c["name"] == "Health" for c in (await b.get("/v1/categories")).json())
    assert (await a.patch(f"/v1/categories/{health['id']}", {})).status_code == 422
    # defaults cannot be deleted
    assert (await a.delete(f"/v1/categories/{health['id']}")).status_code == 404


async def test_delete_custom_category_with_reassign(new_user):
    api = new_user()
    acc = await mk_account(api)
    pets = (await api.post("/v1/categories", {"name": "Pets"})).json()
    sub = (await api.post("/v1/categories", {"name": "Vet", "parent_id": pets["id"]})).json()
    other = await cat(api, "Other")
    await spend(api, acc["id"], pets["id"], 1000, "2026-03-02")
    blocked = await api.delete(f"/v1/categories/{pets['id']}")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "category_has_children"
    assert (await api.delete(f"/v1/categories/{sub['id']}")).status_code == 204
    assert (await api.delete(f"/v1/categories/{pets['id']}?reassign_to={other['id']}")).status_code == 204
    txns = (await api.get("/v1/transactions")).json()["items"]
    assert txns[0]["category_id"] == other["id"]


async def test_budget_rollover_upsert_and_overview(new_user):
    api = new_user()
    acc = await mk_account(api)
    groceries = await cat(api, "Groceries")
    organic = (await api.post("/v1/categories", {"name": "Organic", "parent_id": groceries["id"]})).json()

    # Jan: 1000 limit, 400 spent => 600 unspent. Feb has rollover => 1000 + 600 available.
    jan = await api.put(
        "/v1/budgets", {"category_id": groceries["id"], "month": "2026-01-15", "limit_minor": 100_000}
    )
    assert jan.status_code == 200 and jan.json()["rollover"] is False and jan.json()["month"] == "2026-01-01"
    feb = await api.put(
        "/v1/budgets",
        {"category_id": groceries["id"], "month": "2026-02-01", "limit_minor": 100_000, "rollover": True},
    )
    assert feb.status_code == 200
    # spend in the sub-category counts towards the parent's budget
    await spend(api, acc["id"], groceries["id"], 30_000, "2026-01-10")
    await spend(api, acc["id"], organic["id"], 10_000, "2026-01-20")
    await spend(api, acc["id"], groceries["id"], 25_000, "2026-02-03")
    b = (await api.get("/v1/budgets?month=2026-02-10")).json()[0]
    assert b["rollover"] is True and b["rollover_in_minor"] == 60_000
    assert b["available_minor"] == 160_000 and b["spent_minor"] == 25_000 and b["remaining_minor"] == 135_000

    # chained rollover: March also rolls; unspent Feb (135k) carries
    mar = await api.put(
        "/v1/budgets",
        {"category_id": groceries["id"], "month": "2026-03-01", "limit_minor": 100_000, "rollover": True},
    )
    assert mar.json()["rollover_in_minor"] == 135_000 and mar.json()["available_minor"] == 235_000
    # overspend never carries a negative amount
    await spend(api, acc["id"], groceries["id"], 400_000, "2026-03-05")
    apr = await api.put(
        "/v1/budgets",
        {"category_id": groceries["id"], "month": "2026-04-01", "limit_minor": 100_000, "rollover": True},
    )
    assert apr.json()["rollover_in_minor"] == 0 and apr.json()["available_minor"] == 100_000

    # PATCH toggles rollover / limit; empty patch is rejected
    patched = await api.patch(f"/v1/budgets/{apr.json()['id']}", {"rollover": False, "limit_minor": 5})
    assert patched.json()["rollover"] is False and patched.json()["limit_minor"] == 5
    assert (await api.patch(f"/v1/budgets/{apr.json()['id']}", {})).status_code == 422
    # upsert replaces, does not duplicate
    again = await api.put(
        "/v1/budgets", {"category_id": groceries["id"], "month": "2026-04-01", "limit_minor": 7}
    )
    assert again.json()["id"] == apr.json()["id"] and again.json()["limit_minor"] == 7

    # overview: all expense categories, parent spend includes children
    ov = (await api.get("/v1/budgets/overview?month=2026-01-20")).json()
    rows = {r["category"]["name"]: r for r in ov["rows"]}
    assert "Salary" not in rows and "Credit Card Payments" not in rows  # expense categories only
    assert (
        rows["Groceries"]["spent_minor"] == 40_000 and rows["Groceries"]["budget"]["limit_minor"] == 100_000
    )
    assert rows["Organic"]["spent_minor"] == 10_000 and rows["Organic"]["budget"] is None
    assert rows["Dining & Delivery"]["budget"] is None and rows["Dining & Delivery"]["spent_minor"] == 0
    assert ov["total_limit_minor"] == 100_000 and ov["total_spent_minor"] == 40_000
    await api.patch(f"/v1/categories/{(await cat(api, 'Dining & Delivery'))['id']}", {"is_hidden": True})
    ov = (await api.get("/v1/budgets/overview?month=2026-01-20")).json()
    assert all(r["category"]["name"] != "Dining & Delivery" for r in ov["rows"])


async def test_budget_rejects_foreign_category(new_user):
    a, b = new_user(), new_user()
    mine = (await a.post("/v1/categories", {"name": "Mine"})).json()
    r = await b.put("/v1/budgets", {"category_id": mine["id"], "month": "2026-01-01", "limit_minor": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_category"


@pytest.mark.parametrize("path", ["/v1/categories", "/v1/budgets", "/v1/budgets/overview"])
async def test_auth_required(new_user, path):
    api = new_user()
    r = await api.c.get(path)
    assert r.status_code == 401

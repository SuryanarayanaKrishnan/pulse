"""Classification rules (/v1/rules), the suggested-rule flow, and rules at ingest time."""

from uuid import UUID, uuid4

from app.services.ingest import process_raw_ingest
from tests.integration.test_categories import cat
from tests.integration.test_flows import mk_account


async def txn(api, account_id: str, merchant: str, **over):
    body = {
        "account_id": account_id,
        "amount_minor": 5000,
        "direction": "debit",
        "merchant_raw": merchant,
        "posted_at": "2026-03-01T10:00:00+00:00",
        **over,
    }
    r = await api.post("/v1/transactions", body)
    assert r.status_code == 201, r.text
    return r.json()


def cond(field, **kw):
    return {"field": field, **kw}


async def test_rule_crud_priority_and_validation(new_user):
    api = new_user()
    shopping = await cat(api, "Shopping")
    body = {
        "name": "Big spends",
        "conditions": [cond("amount", op="gte", value_minor=100_000), cond("direction", value="debit")],
        "category_id": shopping["id"],
        "tags": ["big", "big", "review"],
    }
    r = await api.post("/v1/rules", body)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["priority"] == 100 and rule["is_active"] and rule["tags"] == ["big", "review"]
    assert rule["conditions"][0] == {"field": "amount", "op": "gte", "value_minor": 100_000}

    # validation
    assert (await api.post("/v1/rules", {**body, "conditions": []})).status_code == 422
    assert (await api.post("/v1/rules", {"conditions": body["conditions"]})).status_code == 422  # no action
    bad_regex = {**body, "conditions": [cond("merchant", op="regex", value="(a+)+")]}
    assert (await api.post("/v1/rules", bad_regex)).status_code == 422
    unknown_cat = {**body, "category_id": str(uuid4())}
    assert (await api.post("/v1/rules", unknown_cat)).json()["error"]["code"] == "invalid_category"
    foreign_acc = {**body, "conditions": [cond("account", account_id=str(uuid4()))]}
    assert (await api.post("/v1/rules", foreign_acc)).json()["error"]["code"] == "invalid_account"

    # update: merge semantics, must keep an action
    up = await api.patch(f"/v1/rules/{rule['id']}", {"priority": 5, "is_active": False, "name": None})
    assert up.status_code == 200 and up.json()["priority"] == 5 and up.json()["is_active"] is False
    assert up.json()["name"] is None
    stripped = await api.patch(
        f"/v1/rules/{rule['id']}", {"category_id": None, "tags": [], "exclude_from_budget": False}
    )
    assert stripped.status_code == 422 and stripped.json()["error"]["code"] == "rule_without_action"
    assert (await api.get(f"/v1/rules/{rule['id']}")).json()["priority"] == 5

    # ordering: list is by priority; reorder needs every id
    second = (
        await api.post(
            "/v1/rules",
            {"conditions": [cond("merchant", op="contains", value="x")], "tags": ["x"], "priority": 1},
        )
    ).json()
    assert [r["id"] for r in (await api.get("/v1/rules")).json()] == [second["id"], rule["id"]]
    assert (await api.post("/v1/rules/reorder", {"ids": [rule["id"]]})).status_code == 422
    order = await api.post("/v1/rules/reorder", {"ids": [rule["id"], second["id"]]})
    assert [r["id"] for r in order.json()] == [rule["id"], second["id"]]
    assert [r["id"] for r in (await api.get("/v1/rules")).json()] == [rule["id"], second["id"]]

    assert (await api.delete(f"/v1/rules/{rule['id']}")).status_code == 204
    assert (await api.get(f"/v1/rules/{rule['id']}")).status_code == 404


async def test_rules_are_tenant_scoped(new_user):
    a, b = new_user(), new_user()
    shopping = await cat(a, "Shopping")
    rule = (
        await a.post(
            "/v1/rules",
            {"conditions": [cond("merchant", op="contains", value="x")], "category_id": shopping["id"]},
        )
    ).json()
    assert (await b.get("/v1/rules")).json() == []
    for call in (b.get, b.delete):
        assert (await call(f"/v1/rules/{rule['id']}")).status_code == 404
    assert (await b.patch(f"/v1/rules/{rule['id']}", {"name": "n"})).status_code == 404
    assert (await b.post(f"/v1/rules/{rule['id']}/apply", {})).status_code == 404
    acc = await mk_account(a)
    assert (await b.post("/v1/rules/reorder", {"ids": [rule["id"]]})).status_code == 422
    bad = {"conditions": [cond("account", account_id=acc["id"])], "tags": ["t"]}
    assert (await b.post("/v1/rules", bad)).json()["error"]["code"] == "invalid_account"
    assert (await b.post("/v1/rules/preview", {"conditions": bad["conditions"]})).status_code == 422


async def test_preview_and_retroactive_apply(new_user):
    api = new_user()
    acc = await mk_account(api)
    other_acc = await mk_account(api, name="Card", last4="9999")
    shopping, groceries = await cat(api, "Shopping"), await cat(api, "Groceries")
    await txn(api, acc["id"], "CARREFOUR MOE", amount_minor=15_000, posted_at="2026-03-01T10:00:00+00:00")
    await txn(
        api, acc["id"], "CARREFOUR CITY CENTRE", amount_minor=2_500, posted_at="2026-03-02T10:00:00+00:00"
    )
    await txn(
        api, other_acc["id"], "CARREFOUR MOE", amount_minor=15_000, posted_at="2026-03-03T10:00:00+00:00"
    )
    await txn(api, acc["id"], "JOES CAFE", amount_minor=15_000, posted_at="2026-03-04T10:00:00+00:00")
    conditions = [
        cond("merchant", op="contains", value="carrefour"),
        cond("amount", op="between", min_minor=10_000, max_minor=20_000),
        cond("account", account_id=acc["id"]),
    ]
    pv = await api.post(
        "/v1/rules/preview", {"conditions": conditions, "category_id": shopping["id"], "limit": 10}
    )
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["matched_count"] == 1 and body["scanned_count"] == 4 and body["truncated"] is False
    assert body["items"][0]["transaction"]["merchant_normalized"] == "Carrefour"
    assert body["items"][0]["category_would_change"] is True  # groceries -> shopping
    # no category action => nothing would change, and nothing was modified
    plain = (await api.post("/v1/rules/preview", {"conditions": conditions})).json()
    assert plain["items"][0]["category_would_change"] is False
    assert all(
        t["category_id"] != shopping["id"] for t in (await api.get("/v1/transactions")).json()["items"]
    )
    # limit caps items but not the count
    wide = (
        await api.post(
            "/v1/rules/preview",
            {"conditions": [cond("merchant", op="contains", value="carrefour")], "limit": 1},
        )
    ).json()
    assert wide["matched_count"] == 3 and len(wide["items"]) == 1

    rule = (
        await api.post(
            "/v1/rules",
            {
                "conditions": conditions,
                "category_id": shopping["id"],
                "rename_merchant": "Carrefour (big shop)",
                "tags": ["bulk"],
            },
        )
    ).json()
    assert rule["applied_count"] is None
    done = await api.post(f"/v1/rules/{rule['id']}/apply", {})
    assert done.json() == {"matched": 1, "updated": 1}
    items = {
        (t["merchant_normalized"], t["account_id"]): t
        for t in (await api.get("/v1/transactions")).json()["items"]
    }
    hit = items[("Carrefour (big shop)", acc["id"])]
    assert (
        hit["category_id"] == shopping["id"]
        and hit["tags"] == ["bulk"]
        and hit["applied_rule_id"] == rule["id"]
    )
    assert items[("Carrefour", other_acc["id"])]["category_id"] == groceries["id"]
    # idempotent
    assert (await api.post(f"/v1/rules/{rule['id']}/apply", {})).json() == {"matched": 1, "updated": 0}

    # only_uncategorised leaves categorised history alone
    await txn(api, acc["id"], "UNKNOWN SHOP LLC", posted_at="2026-03-05T10:00:00+00:00")
    rule2 = (
        await api.post(
            "/v1/rules",
            {"conditions": [cond("direction", value="debit")], "category_id": groceries["id"]},
        )
    ).json()
    res = (await api.post(f"/v1/rules/{rule2['id']}/apply", {"only_uncategorised": True})).json()
    assert res == {"matched": 2, "updated": 2}  # JOES CAFE and UNKNOWN SHOP; the categorised ones are skipped
    # create with apply_to_existing reports the count
    created = (
        await api.post(
            "/v1/rules",
            {
                "conditions": [cond("merchant", op="equals", value="joes cafe")],
                "category_id": shopping["id"],
                "apply_to_existing": True,
            },
        )
    ).json()
    assert created["applied_count"] == 1


async def test_exclude_and_mark_transfer_actions_affect_budgets(new_user):
    api = new_user()
    acc = await mk_account(api)
    groceries, transfer = await cat(api, "Groceries"), await cat(api, "Transfer")
    await api.put(
        "/v1/budgets", {"category_id": groceries["id"], "month": "2026-03-01", "limit_minor": 50_000}
    )
    await txn(api, acc["id"], "CARREFOUR MOE", amount_minor=10_000)
    gift = await txn(
        api, acc["id"], "CARREFOUR GIFT CARD", amount_minor=7_000, posted_at="2026-03-02T10:00:00+00:00"
    )
    spent = lambda: api.get("/v1/budgets?month=2026-03-01")  # noqa: E731
    assert (await spent()).json()[0]["spent_minor"] == 17_000
    r = await api.post(
        "/v1/rules",
        {
            "conditions": [cond("description", op="contains", value="gift card")],
            "exclude_from_budget": True,
            "apply_to_existing": True,
        },
    )
    assert r.json()["applied_count"] == 1
    assert (await api.get(f"/v1/transactions/{gift['id']}")).json()["exclude_from_budget"] is True
    assert (await spent()).json()[0]["spent_minor"] == 10_000
    # mark_transfer: Transfer category + excluded
    tr = await txn(api, acc["id"], "WISE PAYMENTS", posted_at="2026-03-03T10:00:00+00:00")
    assert tr["category_id"] != transfer["id"]
    rule = (
        await api.post(
            "/v1/rules",
            {"conditions": [cond("merchant", op="contains", value="wise")], "mark_transfer": True},
        )
    ).json()
    await api.post(f"/v1/rules/{rule['id']}/apply", {})
    got = (await api.get(f"/v1/transactions/{tr['id']}")).json()
    assert got["category_id"] == transfer["id"] and got["exclude_from_budget"] is True
    # and new transactions pick it up at creation
    tr2 = await txn(api, acc["id"], "WISE PAYMENTS", posted_at="2026-03-04T10:00:00+00:00")
    assert tr2["category_id"] == transfer["id"] and tr2["exclude_from_budget"] is True


async def test_user_rules_beat_the_dictionary_and_priority_orders_them(new_user):
    api = new_user()
    acc = await mk_account(api)
    shopping, health, dining = (
        await cat(api, "Shopping"),
        await cat(api, "Health"),
        await cat(api, "Dining & Delivery"),
    )
    first = await txn(api, acc["id"], "TALABAT DUBAI")
    assert first["category_id"] == dining["id"]  # dictionary
    low = (
        await api.post(
            "/v1/rules",
            {
                "conditions": [cond("merchant", op="contains", value="talabat")],
                "category_id": health["id"],
                "priority": 50,
            },
        )
    ).json()
    high = (
        await api.post(
            "/v1/rules",
            {
                "conditions": [cond("merchant", op="equals", value="talabat")],
                "category_id": shopping["id"],
                "priority": 10,
                "tags": ["delivery"],
            },
        )
    ).json()
    t = await txn(api, acc["id"], "TALABAT DUBAI", posted_at="2026-03-02T10:00:00+00:00")
    assert t["category_id"] == shopping["id"] and t["applied_rule_id"] == high["id"]
    assert t["tags"] == ["delivery"]
    # inactive rules are ignored; the next one down wins
    await api.patch(f"/v1/rules/{high['id']}", {"is_active": False})
    t2 = await txn(api, acc["id"], "TALABAT DUBAI", posted_at="2026-03-03T10:00:00+00:00")
    assert t2["category_id"] == health["id"] and t2["applied_rule_id"] == low["id"]
    # an explicit category on create still wins over rules
    t3 = await txn(
        api, acc["id"], "TALABAT DUBAI", category_id=dining["id"], posted_at="2026-03-04T10:00:00+00:00"
    )
    assert t3["category_id"] == dining["id"]


async def test_rules_apply_to_sms_ingest(new_user, sessionmaker_):
    api = new_user()
    await mk_account(api, last4="4321", institution="ENBD")
    travel = await cat(api, "Travel")
    await api.post(
        "/v1/rules",
        {
            "conditions": [
                cond("source", value="sms"),
                cond("merchant", op="contains", value="spinneys"),
                cond("amount", op="equals", value_minor=900),
            ],
            "category_id": travel["id"],
            "tags": ["from-sms"],
        },
    )
    sms = {"raw_text": "Purchase of AED 9.00 at SPINNEYS on card ending 4321", "sender": "ENBD"}
    ing = (await api.post("/v1/ingest/sms", sms)).json()
    async with sessionmaker_() as s:
        await process_raw_ingest(s, UUID(ing["id"]))
    t = (await api.get("/v1/transactions?q=spinneys")).json()["items"][0]
    assert t["category_id"] == travel["id"] and t["tags"] == ["from-sms"] and t["source"] == "sms"


async def test_recategorising_returns_suggested_rule(new_user):
    api = new_user()
    acc = await mk_account(api)
    shopping, groceries = await cat(api, "Shopping"), await cat(api, "Groceries")
    t1 = await txn(api, acc["id"], "CARREFOUR MOE")
    await txn(api, acc["id"], "CARREFOUR CITY CENTRE", posted_at="2026-03-02T10:00:00+00:00")
    await txn(api, acc["id"], "CARREFOUR DIFC", posted_at="2026-03-03T10:00:00+00:00")
    r = await api.patch(f"/v1/transactions/{t1['id']}", {"category_id": shopping["id"]})
    assert r.status_code == 200
    out = r.json()
    assert out["category_id"] == shopping["id"] and out["applied_rule_id"] is None
    sug = out["suggested_rule"]
    assert sug["category_id"] == shopping["id"] and sug["matching_count"] == 2
    assert sug["conditions"] == [{"field": "merchant", "op": "equals", "value": "Carrefour"}]
    assert "Carrefour" in sug["name"] and "Shopping" in sug["name"]
    assert (await api.get("/v1/rules")).json() == []  # nothing saved by default

    # the suggestion is a valid POST /v1/rules body: accept it and apply to history
    accept = await api.post(
        "/v1/rules", {**{k: v for k, v in sug.items() if k != "matching_count"}, "apply_to_existing": True}
    )
    assert (
        accept.status_code == 201 and accept.json()["applied_count"] == 3
    )  # also stamps the one already moved
    items = (await api.get("/v1/transactions?q=carrefour")).json()["items"]
    assert {i["category_id"] for i in items} == {shopping["id"]}

    # no suggestion when an equivalent rule exists, when the category did not change, or without a merchant
    t4 = await txn(api, acc["id"], "CARREFOUR MAF", posted_at="2026-03-04T10:00:00+00:00")
    again = await api.patch(f"/v1/transactions/{t4['id']}", {"category_id": groceries["id"]})
    assert again.json()["suggested_rule"] is not None  # groceries is a different target
    same = await api.patch(f"/v1/transactions/{t4['id']}", {"category_id": groceries["id"]})
    assert same.json()["suggested_rule"] is None
    back = await api.patch(f"/v1/transactions/{t4['id']}", {"category_id": shopping["id"]})
    assert back.json()["suggested_rule"] is None  # the saved rule already says so
    nomerchant = await txn(api, acc["id"], None, posted_at="2026-03-05T10:00:00+00:00")
    r = await api.patch(f"/v1/transactions/{nomerchant['id']}", {"category_id": groceries["id"]})
    assert r.json()["suggested_rule"] is None
    # notes-only edit never suggests
    r = await api.patch(f"/v1/transactions/{t4['id']}", {"notes": "hi", "tags": ["a"]})
    assert r.json()["suggested_rule"] is None and r.json()["tags"] == ["a"]
    # opt-in remember_merchant keeps working
    t5 = await txn(api, acc["id"], "LULU HYPERMARKET", posted_at="2026-03-06T10:00:00+00:00")
    saved = await api.patch(
        f"/v1/transactions/{t5['id']}", {"category_id": shopping["id"], "remember_merchant": True}
    )
    assert saved.json()["suggested_rule"] is None
    assert [r["merchant_key"] for r in (await api.get("/v1/merchant-rules")).json()] == ["lulu"]


async def test_legacy_merchant_rule_is_a_regular_rule(new_user):
    api = new_user()
    acc = await mk_account(api)
    shopping = await cat(api, "Shopping")
    assert (
        await api.post("/v1/merchant-rules", {"merchant": "carrefour", "category_id": shopping["id"]})
    ).status_code == 201
    rules = (await api.get("/v1/rules")).json()
    assert len(rules) == 1 and rules[0]["priority"] == 1000
    assert rules[0]["conditions"] == [{"field": "merchant", "op": "equals", "value": "carrefour"}]
    t = await txn(api, acc["id"], "CARREFOUR MOE")
    assert t["category_id"] == shopping["id"]
    # editing its conditions detaches it from the /merchant-rules shortcut list
    await api.patch(
        f"/v1/rules/{rules[0]['id']}", {"conditions": [cond("merchant", op="contains", value="carre")]}
    )
    assert (await api.get("/v1/merchant-rules")).json() == []
    # and authored rules sit ahead of the learnt ones by default
    health = await cat(api, "Health")
    await api.post("/v1/merchant-rules", {"merchant": "lulu", "category_id": shopping["id"]})
    authored = (
        await api.post(
            "/v1/rules",
            {"conditions": [cond("merchant", op="contains", value="lulu")], "category_id": health["id"]},
        )
    ).json()
    assert [r["id"] for r in (await api.get("/v1/rules")).json()][0] == authored["id"]

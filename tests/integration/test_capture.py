"""Step 2 (automatic capture) integration tests: categorisation rules, statements, Gmail."""

from uuid import UUID

from app.services.ingest import process_raw_ingest
from tests.integration.test_flows import mk_account


async def _category_id(api, name: str) -> str:
    cats = (await api.get("/v1/categories")).json()
    return next(c["id"] for c in cats if c["name"] == name)


async def _create_txn(api, account_id: str, merchant: str, **over):
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


async def test_manual_txn_is_categorised_by_dictionary(new_user):
    api = new_user()
    acc = await mk_account(api)
    t = await _create_txn(api, acc["id"], "TALABAT DUBAI AE")
    assert t["merchant_normalized"] == "Talabat"
    assert t["category_id"] == await _category_id(api, "Dining")
    explicit = await _create_txn(
        api,
        acc["id"],
        "TALABAT",
        category_id=await _category_id(api, "Shopping"),
        posted_at="2026-03-02T10:00:00+00:00",
    )
    assert explicit["category_id"] == await _category_id(api, "Shopping")


async def test_override_becomes_rule_applied_to_future_ingests(new_user, sessionmaker_):
    api = new_user()
    acc = await mk_account(api, last4="4321", institution="ENBD")
    t = await _create_txn(api, acc["id"], "CARREFOUR MOE")
    other = await _create_txn(api, acc["id"], "CARREFOUR CITY CENTRE", posted_at="2026-03-02T10:00:00+00:00")
    shopping = await _category_id(api, "Shopping")
    assert t["category_id"] == await _category_id(api, "Groceries")

    r = await api.patch(f"/v1/transactions/{t['id']}", {"category_id": shopping})
    assert r.status_code == 200 and r.json()["category_id"] == shopping
    rules = (await api.get("/v1/merchant-rules")).json()
    assert [(x["merchant_key"], x["category_id"]) for x in rules] == [("carrefour", shopping)]
    # Not retroactive by default.
    assert (await api.get(f"/v1/transactions/{other['id']}")).json()["category_id"] != shopping

    # New SMS ingest for the same merchant uses the rule.
    sms = {"raw_text": "Purchase of AED 9.00 at CARREFOUR MOE on card ending 4321", "sender": "ENBD"}
    ing = (await api.post("/v1/ingest/sms", sms)).json()
    async with sessionmaker_() as s:
        await process_raw_ingest(s, UUID(ing["id"]))
    newest = (await api.get("/v1/transactions?q=carrefour&limit=10")).json()["items"]
    sms_txn = next(x for x in newest if x["source"] == "sms")
    assert sms_txn["category_id"] == shopping

    # Opt-out: remember_merchant=false creates no rule; apply_to_existing re-categorises history.
    third = await _create_txn(api, acc["id"], "LULU HYPERMARKET")
    await api.patch(f"/v1/transactions/{third['id']}", {"category_id": shopping, "remember_merchant": False})
    assert len((await api.get("/v1/merchant-rules")).json()) == 1
    created = await api.post(
        "/v1/merchant-rules", {"merchant": "carrefour", "category_id": shopping, "apply_to_existing": True}
    )
    assert created.status_code == 201
    assert (await api.get(f"/v1/transactions/{other['id']}")).json()["category_id"] == shopping
    assert (await api.delete(f"/v1/merchant-rules/{rules[0]['id']}")).status_code == 204
    assert (await api.get("/v1/merchant-rules")).json() == []


async def test_rules_are_tenant_scoped(new_user):
    a, b = new_user(), new_user()
    acc = await mk_account(a)
    t = await _create_txn(a, acc["id"], "CARREFOUR")
    await a.patch(f"/v1/transactions/{t['id']}", {"category_id": await _category_id(a, "Shopping")})
    rule = (await a.get("/v1/merchant-rules")).json()[0]
    assert (await b.get("/v1/merchant-rules")).json() == []
    assert (await b.delete(f"/v1/merchant-rules/{rule['id']}")).status_code == 404
    bad = await a.post(
        "/v1/merchant-rules", {"merchant": "x", "category_id": "00000000-0000-0000-0000-000000000001"}
    )
    assert bad.status_code == 422

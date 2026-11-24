"""Card catalog, user cards, rewards summary, best-card ranking and the wallet-tap nudge."""

import json
from datetime import UTC, datetime

import pytest

from app.services.card_catalog import UPSERT
from app.services.rewards import parse_structure
from tests.integration.test_flows import mk_account

EXCL = ["fees", "cc_payment", "transfer", "rent", "education", "utilities"]


def _structure(base, rules):
    return {
        "unit": "cashback",
        "point_value_aed": 1,
        "base_rate": base,
        "rules": rules,
        "excluded_categories": EXCL,
    }


# Fixed fixtures independent of the live catalog (whose rates change): the numbers below are asserted exactly.
TEST_CARDS = {
    "Test Bank Dining Cashback": _structure(
        0.005, [{"name": "Dining", "rate": 0.05, "categories": ["dining"], "monthly_cap_units": 300}]
    ),
    "Test Bank Plain Cashback": _structure(
        0.01, [{"name": "Dining", "rate": 0.03, "categories": ["dining"], "monthly_cap_units": 100}]
    ),
}
MASH, FAB = "Test Bank Dining Cashback", "Test Bank Plain Cashback"


@pytest.fixture(autouse=True)
async def test_cards(sessionmaker_):
    async with sessionmaker_() as s:
        for name, rs in TEST_CARDS.items():
            await s.execute(
                UPSERT,
                {
                    "issuer": "Test Bank",
                    "name": name.removeprefix("Test Bank "),
                    "network": "visa",
                    "fee": 0,
                    "salary": 500_000,
                    "rs": json.dumps(rs),
                    "perks": "{}",
                    "source_url": None,
                    "verified_at": None,
                },
            )
        await s.commit()


async def catalog(api) -> dict[str, dict]:
    return {f"{c['issuer']} {c['name']}": c for c in (await api.get("/v1/cards/catalog")).json()}


async def cat_id(api, name: str) -> str:
    cats = (await api.get("/v1/categories")).json()
    return next(c["id"] for c in cats if c["name"] == name)


async def add_card(api, cat: dict, last4: str, **acc):
    account = await mk_account(
        api, type="credit_card", name=cat["name"], last4=last4, credit_limit_minor=5_000_000, **acc
    )
    r = await api.post("/v1/cards", {"catalog_card_id": cat["id"], "account_id": account["id"]})
    assert r.status_code == 201, r.text
    return r.json(), account


async def spend(api, account_id: str, category_id: str | None, amount: int, merchant="Cafe"):
    body = {
        "account_id": account_id,
        "amount_minor": amount,
        "direction": "debit",
        "merchant_normalized": merchant,
        "posted_at": datetime.now(UTC).isoformat(),
    }
    if category_id:
        body["category_id"] = category_id
    assert (await api.post("/v1/transactions", body)).status_code == 201


async def test_loaded_catalog_parses_and_flags_verification(new_user):
    api = new_user()
    cards = (await api.get("/v1/cards/catalog")).json()
    assert len(cards) >= 100
    real = [c for c in cards if c["perks"].get("catalog_id")]
    assert len(real) >= 100
    for c in cards:
        parse_structure(c["reward_structure"])  # every structure is valid
        assert "source" in c
        if c["data_status"] == "verified":
            assert c["verified_on"] and c["source"] and c["perks"]["confidence"] == "high"
        else:
            assert c["verified_on"] is None
    assert any(c["data_status"] == "verified" for c in real) and any(
        c["data_status"] == "unverified" for c in real
    )


async def test_add_and_remove_cards_validates_account(new_user):
    a, b = new_user(), new_user()
    cat = (await catalog(a))[MASH]
    bank = await mk_account(a, last4="1000")
    assert (
        await a.post("/v1/cards", {"catalog_card_id": cat["id"], "account_id": bank["id"]})
    ).status_code == 422
    uc, acc = await add_card(a, cat, "2000")
    assert [c["id"] for c in (await a.get("/v1/cards")).json()] == [uc["id"]]
    assert (await b.get("/v1/cards")).json() == []
    assert (
        await b.post("/v1/cards", {"catalog_card_id": cat["id"], "account_id": acc["id"]})
    ).status_code == 404
    assert (await b.delete(f"/v1/cards/{uc['id']}")).status_code == 404
    assert (await a.delete(f"/v1/cards/{uc['id']}")).status_code == 204
    assert (await a.get("/v1/cards")).json() == []


async def test_summary_tracks_cap_usage_and_cycle(new_user):
    api = new_user()
    cat = (await catalog(api))[MASH]  # dining 5%, cap AED 300
    uc, acc = await add_card(api, cat, "3000")
    dining = await cat_id(api, "Dining & Delivery")
    await spend(api, acc["id"], dining, 400_000)  # 5% of 4000 = 200
    s = (await api.get("/v1/rewards/summary")).json()
    row = next(c for c in s["cards"] if c["user_card_id"] == uc["id"])
    assert row["unit"] == "cashback" and row["earned_value_minor"] == 20_000 and row["spend_minor"] == 400_000
    cap = next(c for c in row["caps"] if c["rule"] == "Dining")
    assert float(cap["used_units"]) == 200 and float(cap["remaining_units"]) == 100
    assert float(cap["pct_used"]) > 66 and row["data_status"] == "unverified"
    assert row["cycle_start"] <= s["as_of"] <= row["cycle_end"] and row["days_left"] >= 0
    # spend beyond the cap earns nothing more
    await spend(api, acc["id"], dining, 1_000_000)
    row = next(c for c in (await api.get("/v1/rewards/summary")).json()["cards"])
    assert row["earned_value_minor"] == 30_000 and float(row["caps"][0]["remaining_units"]) == 0


async def test_best_card_prefers_headroom_and_validates_input(new_user):
    api = new_user()
    cats = await catalog(api)
    mash, fab = cats[MASH], cats[FAB]
    _, a1 = await add_card(api, mash, "4001")
    uc2, _ = await add_card(api, fab, "4002")
    body = {"category": "dining", "amount_minor": 10_000}
    ranked = (await api.post("/v1/rewards/best-card", body)).json()["ranked"]
    assert ranked[0]["name"] == mash["name"] and ranked[0]["estimated_value_minor"] == 500
    # Mashreq's dining cap is used up -> the FAB card wins
    await spend(api, a1["id"], await cat_id(api, "Dining & Delivery"), 7_000_000)
    ranked = (await api.post("/v1/rewards/best-card", body)).json()["ranked"]
    assert ranked[0]["user_card_id"] == uc2["id"] and ranked[0]["estimated_value_minor"] == 300
    assert ranked[1]["estimated_value_minor"] == 0 and ranked[1]["hits_cap"]
    assert (await api.post("/v1/rewards/best-card", {"amount_minor": 100})).status_code == 422
    assert (
        await api.post("/v1/rewards/best-card", {"category": "dining", "amount_minor": 0})
    ).status_code == 422
    assert (await new_user().post("/v1/rewards/best-card", body)).json()["ranked"] == []


async def test_wallet_tap_returns_nudge_when_cap_is_hit(new_user):
    api = new_user()
    cats = await catalog(api)
    _, acc = await add_card(api, cats[MASH], "5001")
    await add_card(api, cats[FAB], "5002")
    tok = (await api.post("/v1/ingest/token")).json()["token"]
    now = datetime.now(UTC).isoformat()

    async def tap(amount, last4="5001"):
        r = await api.c.post(
            "/v1/ingest/apple-shortcut",
            json={"amount": amount, "merchant": "TALABAT", "last4": last4, "timestamp": now},
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 202, r.text
        return r.json()

    assert (await tap(100))["nudge"] is None  # 5 of 300 used
    near = (await tap(5_600))["nudge"]  # ~280 of 300
    assert near["level"] == "near_cap" and near["suggested_card_id"] is None  # still the best card
    capped = (await tap(500))["nudge"]
    assert capped["level"] == "capped" and capped["extra_value_minor"] > 0
    # the card with no competitor never gets a suggestion
    other = (await tap(100, "5002"))["nudge"]
    assert other is None

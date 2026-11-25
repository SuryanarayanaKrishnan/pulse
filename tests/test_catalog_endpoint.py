from app.services.card_catalog import load_catalog, public_catalog


async def test_catalog_is_public_and_versioned(make_client):
    r = await make_client().get("/v1/catalog/cards")  # no bearer token
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["version"], int) and body["version"] > 20000101
    assert body["updated_on"]
    assert len(body["cards"]) >= 100
    assert "max-age" in r.headers["cache-control"]


async def test_each_card_has_what_the_app_reads(make_client):
    cards = (await make_client().get("/v1/catalog/cards")).json()["cards"]
    ids = set()
    for c in cards:
        assert {"id", "issuer", "name", "reward_structure", "perks", "annual_fee_minor"} <= c.keys()
        assert c["id"] not in ids
        ids.add(c["id"])
        assert isinstance(c["annual_fee_minor"], int)
        assert "rules" in c["reward_structure"] or "base_rate" in c["reward_structure"]


def test_discontinued_cards_are_left_out():
    cat = load_catalog()
    cat = {**cat, "cards": [*cat["cards"], {**cat["cards"][0], "id": "gone", "status": "discontinued"}]}
    assert "gone" not in {c["id"] for c in public_catalog(cat)["cards"]}


def test_version_is_the_date_as_a_number():
    assert public_catalog({"catalog_version": "2026-11-24", "cards": []})["version"] == 20261124

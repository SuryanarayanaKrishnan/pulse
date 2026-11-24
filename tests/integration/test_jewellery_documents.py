"""Gold jewellery (live melt value vs purchase price, valuation history) and documents with reminders."""

from datetime import timedelta

import pytest
from sqlalchemy import delete

from app.models.market import MarketQuote
from app.services.insights_service import today_dubai
from app.services.valuations.providers import Providers
from app.services.valuations.service import refresh_all
from tests.fake_markets import FakeFx, FakeMetal

SHA = "a" * 64


@pytest.fixture(autouse=True)
async def clean_quotes(sessionmaker_):
    async with sessionmaker_() as s:
        await s.execute(delete(MarketQuote))
        await s.commit()
    yield
    async with sessionmaker_() as s:
        await s.execute(delete(MarketQuote))
        await s.commit()


RING = {
    "description": "22k bangle set", "weight_grams": "10", "karat": 22, "making_charges_minor": 30_000,
    "purchase_price_minor": 250_000, "purchase_date": "2025-11-01", "photo_keys": ["device://photos/1.jpg"],
}  # fmt: skip


async def test_jewellery_valuation_gain_loss_and_history(new_user, sessionmaker_):
    api = new_user()
    r = await api.post("/v1/jewellery", RING)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["fineness"] == "0.9167" and j["valuation"]["basis"] == "cost_basis"  # no gold quote yet
    assert j["valuation"]["market_value_minor"] is None and j["valuation"]["gain_loss_minor"] is None
    async with sessionmaker_() as s:
        await refresh_all(s, Providers(gold=FakeMetal("2000"), fx=FakeFx()))
    j = (await api.get(f"/v1/jewellery/{j['asset_id']}")).json()
    v = j["valuation"]
    assert v["basis"] == "market" and v["market_value_minor"] == 216_468  # 10 g x 22/24 x spot, in fils
    assert v["gain_loss_minor"] == 216_468 - 250_000 and v["gain_loss_pct"] == -13.4
    assert v["per_gram_aed_minor"] == 21_648 and "melt value" in v["explanation"] and v["last_updated"]
    # it is a normal asset too: shows in the asset list and in net worth
    assets = (await api.get("/v1/assets")).json()
    assert assets[0]["kind"] == "gold_jewellery" and assets[0]["current_value_minor"] == 216_468
    assert (await api.get("/v1/net-worth")).json()["assets_by_kind"]["gold_jewellery"] == 216_468
    hist = (await api.get(f"/v1/assets/{j['asset_id']}/valuation-history")).json()
    assert [p["value_minor"] for p in hist["points"]] == [216_468]
    # gold moves: the same-day point is replaced, and edits re-value
    async with sessionmaker_() as s:
        await refresh_all(s, Providers(gold=FakeMetal("2200")))
    hist = (await api.get(f"/v1/assets/{j['asset_id']}/valuation-history")).json()
    assert len(hist["points"]) == 1 and hist["points"][0]["value_minor"] == 238_115
    up = (await api.patch(f"/v1/jewellery/{j['asset_id']}", {"weight_grams": "20", "karat": 18})).json()
    assert (
        up["fineness"] == "0.7500" and up["valuation"]["market_value_minor"] == 389_643
    )  # 20 g x 18/24 x 2200 spot
    purity = (await api.patch(f"/v1/jewellery/{j['asset_id']}", {"purity": "0.999"})).json()
    assert purity["purity"] == "0.9990" and purity["fineness"] == "0.9990"


async def test_jewellery_validation_backup_and_isolation(new_user):
    a, b = new_user(), new_user()
    assert (await a.post("/v1/jewellery", {"description": "x", "weight_grams": "5"})).status_code == 422
    assert (await a.post("/v1/jewellery", {**RING, "weight_grams": "0"})).status_code == 422
    j = (await a.post("/v1/jewellery", RING)).json()
    assert j["photo_keys"] == ["device://photos/1.jpg"] and j["backup_blob"] is None
    blob = {"key": "backups/u/1.enc", "size": 1234, "sha256": SHA, "encrypted": True}
    got = (await a.put(f"/v1/jewellery/{j['asset_id']}/backup", blob)).json()
    assert got["backup_blob"]["key"] == "backups/u/1.enc" and got["backup_blob"]["updated_at"]
    assert (
        await a.put(f"/v1/jewellery/{j['asset_id']}/backup", {**blob, "encrypted": False})
    ).status_code == 422
    assert (await b.get(f"/v1/jewellery/{j['asset_id']}")).status_code == 404
    assert (await b.get("/v1/jewellery")).json() == []
    assert (await a.delete(f"/v1/jewellery/{j['asset_id']}")).status_code == 204
    assert (await a.get(f"/v1/jewellery/{j['asset_id']}")).status_code == 404 and (
        await a.get("/v1/assets")
    ).json() == []


async def test_documents_reminders_and_upcoming_due_in_insights(new_user):
    api = new_user()
    today = today_dubai()
    fd_asset = (
        await api.post(
            "/v1/assets",
            {
                "kind": "fd",
                "name": "ENBD FD",
                "manual_value_minor": 5_000_000,
                "meta": {"maturity_date": (today + timedelta(days=20)).isoformat()},
            },
        )
    ).json()
    far_fd = await api.post(
        "/v1/assets",
        {
            "kind": "fd",
            "name": "Far FD",
            "meta": {"maturity_date": (today + timedelta(days=400)).isoformat()},
        },
    )
    assert far_fd.status_code == 201
    deed = (
        await api.post(
            "/v1/documents",
            {
                "kind": "title_deed",
                "title": "Marina flat deed",
                "institution": "DLD",
                "reference": "TD-1",
                "notes": "Original in the bank locker",
                "reminders": [
                    {
                        "kind": "renewal",
                        "due_on": (today + timedelta(days=45)).isoformat(),
                        "label": "Insurance renewal",
                        "remind_days_before": 60,
                    }
                ],
            },
        )
    ).json()
    assert deed["next_due_on"] == (today + timedelta(days=45)).isoformat() and deed["has_backup"] is False
    fd = (
        await api.post(
            "/v1/documents",
            {
                "kind": "fd_certificate",
                "title": "FD certificate",
                "asset_id": fd_asset["id"],
                "amount_minor": 5_000_000,
            },
        )
    ).json()
    overdue = (
        await api.post(
            f"/v1/documents/{fd['id']}/reminders",
            {"kind": "expiry", "due_on": (today - timedelta(days=3)).isoformat()},
        )
    ).json()
    assert overdue["days_left"] == -3
    ins = (await api.get("/v1/insights/reminders?days=90")).json()
    kinds = [(i["title"], i["status"]) for i in ins["items"]]
    assert kinds == [
        ("FD certificate", "overdue"),
        ("ENBD FD matures", "due_soon"),
        ("Insurance renewal", "due_soon"),  # 45 days away is inside its own 60-day lead
    ]
    assert ins["next_due"]["title"] == "FD certificate" and ins["next_due"]["days_left"] == -3
    assert (await api.get("/v1/insights/reminders?days=10")).json()["items"][0]["status"] == "overdue"
    assert len((await api.get("/v1/insights/reminders?days=10")).json()["items"]) == 1
    # completing a reminder removes it
    done = (await api.post(f"/v1/documents/{fd['id']}/reminders/{overdue['id']}/done")).json()
    assert done["done_at"]
    assert [i["title"] for i in (await api.get("/v1/insights/reminders?days=90")).json()["items"]][
        0
    ] == "ENBD FD matures"
    assert (await api.get(f"/v1/documents/{fd['id']}")).json()["next_due_on"] is None


async def test_document_crud_backup_metadata_and_tenancy(new_user):
    a, b = new_user(), new_user()
    bad_kind = await a.post("/v1/documents", {"kind": "selfie", "title": "x"})
    assert bad_kind.status_code == 422
    assert (
        await a.post(
            "/v1/documents",
            {"kind": "other", "title": "x", "asset_id": "00000000-0000-0000-0000-000000000001"},
        )
    ).status_code == 422
    d = (
        await a.post("/v1/documents", {"kind": "insurance", "title": "Car insurance", "institution": "AXA"})
    ).json()
    up = (
        await a.patch(f"/v1/documents/{d['id']}", {"title": "Car insurance 2026", "notes": "policy 99"})
    ).json()
    assert up["title"] == "Car insurance 2026" and up["notes"] == "policy 99"
    blob = {"storage_key": "docs/u/1.enc", "size": 99, "sha256": SHA, "encrypted": True}
    assert (await a.put(f"/v1/documents/{d['id']}/backup", blob)).json()["has_backup"] is True
    assert (await a.put(f"/v1/documents/{d['id']}/backup", {**blob, "encrypted": False})).status_code == 422
    r = (
        await a.post(f"/v1/documents/{d['id']}/reminders", {"kind": "renewal", "due_on": "2027-01-01"})
    ).json()
    assert (await b.get(f"/v1/documents/{d['id']}")).status_code == 404
    assert (
        await b.post(f"/v1/documents/{d['id']}/reminders", {"kind": "custom", "due_on": "2027-01-01"})
    ).status_code == 404
    assert (await b.delete(f"/v1/documents/{d['id']}/reminders/{r['id']}")).status_code == 404
    assert (await a.delete(f"/v1/documents/{d['id']}/reminders/{r['id']}")).status_code == 204
    assert (await a.delete(f"/v1/documents/{d['id']}")).status_code == 204
    assert (await a.get("/v1/documents")).json() == []

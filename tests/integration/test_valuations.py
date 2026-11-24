"""Cached quotes, automatic asset valuation, graceful fallback and the cron task (fake providers only)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from app.models.asset import Asset
from app.models.market import MarketQuote
from app.services.valuations.providers import ProviderError, Providers, StaticPropertyIndex
from app.services.valuations.service import refresh_all, refresh_quotes
from app.workers.tasks import refresh_valuations
from tests.integration.test_flows import mk_account


@pytest.fixture(autouse=True)
async def clean_quotes(sessionmaker_):
    """Market quotes are global, so start (and leave) every test with an empty cache."""
    async with sessionmaker_() as s:
        await s.execute(delete(MarketQuote))
        await s.commit()
    yield
    async with sessionmaker_() as s:
        await s.execute(delete(MarketQuote))
        await s.commit()


class FakeGold:
    name = "fake-gold"

    def __init__(self, price="2000", fail=False):
        self.price, self.fail = Decimal(price), fail

    async def usd_per_troy_oz(self):
        if self.fail:
            raise ProviderError("gold down")
        return self.price


class FakeFx:
    name = "fake-fx"

    def __init__(self, rates=None, fail=False):
        self.rates, self.fail = rates or {"USD": Decimal("3.6725"), "EUR": Decimal("4.0")}, fail

    async def aed_per_unit(self, currencies):
        if self.fail:
            raise ProviderError("fx down")
        return {c: r for c, r in self.rates.items() if c in currencies}


GOLD_BODY = {
    "kind": "gold", "name": "Gold", "quantity": "100", "unit": "g", "currency": "AED",
    "manual_value_minor": 2_000_000, "valuation_source": "market", "meta": {"purity": "22k"},
}  # fmt: skip


async def get_asset(api, asset_id):
    return (await api.get(f"/v1/assets/{asset_id}")).json()


async def test_gold_asset_before_and_after_refresh_with_manual_fallback(new_user, sessionmaker_):
    api = new_user()
    a = (await api.post("/v1/assets", GOLD_BODY)).json()
    # no quotes cached anywhere yet (or none we control): value falls back to the manual figure with a reason
    assert a["valuation_source"] == "market" and a["manual_value_minor"] == 2_000_000
    async with sessionmaker_() as s:
        await refresh_quotes(s, Providers(gold=FakeGold("2000"), fx=FakeFx()))
        ok, bad = 0, 0
        from app.services.valuations.service import revalue_assets

        ok, bad = await revalue_assets(s)
    assert ok >= 1
    got = await get_asset(api, a["id"])
    assert got["value_basis"] == "market" and got["current_value_minor"] == 2_164_683
    assert (
        got["last_updated"] and "216.47" in got["valuation_note"] and got["manual_value_minor"] == 2_000_000
    )
    # the price source dies: the last quote is kept, and used until it goes stale
    async with sessionmaker_() as s:
        rep = await refresh_quotes(s, Providers(gold=FakeGold(fail=True), fx=FakeFx(fail=True)))
        assert rep.gold == "failed" and rep.fx == "failed" and len(rep.errors) == 2
    assert (await get_asset(api, a["id"]))["value_basis"] == "market"
    # age the data beyond the staleness window: graceful fallback to the manual value
    async with sessionmaker_() as s:
        await s.execute(
            update(Asset)
            .where(Asset.id == a["id"])
            .values(market_valued_at=datetime.now(UTC) - timedelta(days=5))
        )
        await s.commit()
    old = await get_asset(api, a["id"])
    assert old["value_basis"] == "manual" and old["current_value_minor"] == 2_000_000
    assert "stale" in old["valuation_note"] and old["last_updated"] == old["valued_at"]


async def test_asset_without_purity_says_what_is_missing(new_user):
    api = new_user()
    a = (await api.post("/v1/assets", {**GOLD_BODY, "meta": {}})).json()
    assert a["value_basis"] == "manual" and a["current_value_minor"] == 2_000_000
    assert "purity" in a["valuation_note"]
    fixed = (await api.patch(f"/v1/assets/{a['id']}", {"meta": {"purity": "24k"}})).json()
    # patched from cache only: still falls back unless a gold quote exists, never raises
    assert fixed["current_value_minor"] is not None


async def test_manual_assets_are_untouched_and_unsupported_kinds_explain(new_user, sessionmaker_):
    api = new_user()
    manual = (
        await api.post("/v1/assets", {"kind": "cash", "name": "Safe", "manual_value_minor": 123_00})
    ).json()
    assert manual["value_basis"] == "manual" and manual["valuation_note"] is None and manual["last_updated"]
    stock = (
        await api.post(
            "/v1/assets",
            {"kind": "stock", "name": "X", "manual_value_minor": 5_00, "valuation_source": "market"},
        )
    ).json()
    assert stock["value_basis"] == "manual" and "metadata.ticker" in stock["valuation_note"]
    car = (
        await api.post(
            "/v1/assets",
            {"kind": "vehicle", "name": "Car", "manual_value_minor": 9_00, "valuation_source": "market"},
        )
    ).json()
    assert car["value_basis"] == "manual" and "gold" in car["valuation_note"]
    bare = (
        await api.post("/v1/assets", {"kind": "other", "name": "Thing", "cost_basis_minor": 77_00})
    ).json()
    assert bare["value_basis"] == "cost_basis" and bare["current_value_minor"] == 77_00


async def test_property_index_estimate_and_quotes_endpoint(new_user, sessionmaker_):
    api = new_user()
    prop = (
        await api.post(
            "/v1/assets",
            {
                "kind": "real_estate", "name": "Marina flat", "cost_basis_minor": 1_000_000_00, "acquired_on": "2022-01-01",
                "manual_value_minor": 1_100_000_00, "valuation_source": "estimate", "meta": {"index": "dubai_apts_test"},
            },
        )
    ).json()  # fmt: skip
    assert prop["value_basis"] == "manual" and "No index data" in prop["valuation_note"]
    providers = Providers(
        gold=FakeGold("2000"), fx=FakeFx(),
        property_index=StaticPropertyIndex({"dubai_apts_test": {"2022-01-01": 100, "2026-01-01": 150}}),
    )  # fmt: skip
    async with sessionmaker_() as s:
        rep = await refresh_all(s, providers)
    assert (rep.gold, rep.fx, rep.property_index) == ("ok", "ok", "ok") and rep.errors == []
    got = await get_asset(api, prop["id"])
    assert got["value_basis"] == "estimate" and got["current_value_minor"] == 1_500_000_00
    assert "estimate, not a valuation" in got["valuation_note"]
    q = (await api.get("/v1/market/quotes")).json()
    assert (
        q["gold"]["status"] == "live"
        and q["gold"]["usd_per_oz"] == "2000.00000000"
        or float(q["gold"]["usd_per_oz"]) == 2000
    )
    assert float(q["gold"]["aed_per_gram"]["22k"]) == 216.47 and q["gold"]["source"] == "fake-gold"
    assert any(f["currency"] == "USD" and f["status"] == "live" for f in q["fx"])
    assert any(
        i["key"] == "dubai_apts_test" and float(i["latest_value"]) == 150 for i in q["property_indices"]
    )
    # idempotent: refreshing again upserts instead of piling up rows
    async with sessionmaker_() as s:
        await refresh_all(s, providers)
        n = len(
            (await s.execute(select(MarketQuote).where(MarketQuote.symbol == "XAU_USD_OZ"))).scalars().all()
        )
        assert n == 1  # one row per symbol per day


async def test_foreign_currency_uses_cached_fx_in_net_worth(new_user, sessionmaker_):
    api = new_user()
    await api.post(
        "/v1/assets", {"kind": "cash", "name": "Euros", "currency": "EUR", "manual_value_minor": 100_00}
    )
    before = (await api.get("/v1/net-worth")).json()
    assert before["unconverted_currencies"] == ["EUR"] and before["total_assets_minor"] == 0
    async with sessionmaker_() as s:
        await refresh_quotes(s, Providers(fx=FakeFx({"EUR": Decimal("4.0"), "USD": Decimal("3.6725")})))
    after = (await api.get("/v1/net-worth")).json()
    assert after["unconverted_currencies"] == [] and after["total_assets_minor"] == 400_00
    assert float(after["fx_rates"]["EUR"]) == 4.0 and after["fx_last_updated"]
    assert (await api.get("/v1/net-worth?usd_rate=4")).json()["total_assets_minor"] == 400_00


async def test_quotes_endpoint_for_a_fresh_system_and_cron_task(new_user, sessionmaker_):
    api = new_user()
    q = (await api.get("/v1/market/quotes")).json()
    assert q["gold"]["status"] == "unavailable" and q["gold"]["aed_per_gram"] == {}
    assert q["fx"] == [
        {"currency": "USD", "aed_per_unit": "3.6725", "status": "peg", "last_updated": None, "source": "peg"}
    ]
    await mk_account(api)
    out = await refresh_valuations(
        {
            "session_factory": sessionmaker_,
            "valuation_providers": Providers(gold=FakeGold(fail=True), fx=FakeFx()),
        }
    )
    assert out["gold"] == "failed" and out["fx"] == "ok" and out["errors"] == ["gold down"]

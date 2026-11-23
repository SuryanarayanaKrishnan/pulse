"""Pure valuation maths and the HTTP providers (against a mock transport: no network)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.services.valuations import pricing as p
from app.services.valuations.providers import (
    GoldApiProvider,
    OpenErApiFxProvider,
    PegFxProvider,
    ProviderError,
    StaticPropertyIndex,
)

D = Decimal
NOW = datetime(2026, 6, 15, 12, tzinfo=UTC)


def test_purity_parsing():
    assert p.purity_fraction("22k") == D(22) / 24 and p.purity_fraction("24K") == 1
    assert p.purity_fraction("916") == D("0.916") and p.purity_fraction("0.995") == D("0.995")
    for bad in ("", "gold", "30k", "0k", "5", None, "1.5"):
        assert p.purity_fraction(bad) is None


def test_gold_value_per_gram_and_units():
    per_g = p.gold_aed_per_gram(D(2000), D("3.6725"), D(22) / 24)
    assert round(per_g, 2) == D("216.47")
    v, why = p.value_gold(D(100), "g", {"purity": "22k"}, D(2000), NOW, D("3.6725"))
    assert v and why == "" and v.value_minor == 2_164_683 and v.as_of == NOW and "216.47" in v.note
    kg, _ = p.value_gold(D("0.1"), "kg", {"purity": "22k"}, D(2000), NOW, D("3.6725"))
    assert kg and kg.value_minor == v.value_minor
    oz, _ = p.value_gold(D(1), "oz", {"purity": "24k"}, D(2000), NOW, D("3.6725"))
    assert oz and oz.value_minor == 734_500  # one troy ounce of pure gold = USD price x peg


@pytest.mark.parametrize(
    ("qty", "unit", "meta", "price", "fragment"),
    [
        (None, "g", {"purity": "22k"}, D(2000), "quantity"),
        (D(10), None, {"purity": "22k"}, D(2000), "unit"),
        (D(10), "stone", {"purity": "22k"}, D(2000), "not supported"),
        (D(10), "g", {}, D(2000), "purity"),
        (D(10), "g", {"purity": "22k"}, None, "No gold price"),
    ],
)
def test_gold_value_explains_why_it_cannot_value(qty, unit, meta, price, fragment):
    v, why = p.value_gold(qty, unit, meta, price, NOW if price else None, D("3.6725"))
    assert v is None and fragment in why


SERIES = [(date(2022, 1, 1), D(100)), (date(2024, 1, 1), D(120)), (date(2026, 1, 1), D(150))]


def test_property_index_scales_the_base_value():
    meta = {"index": "dubai", "base_date": "2022-01-01"}
    v, _ = p.value_property(meta, 1_000_000_00, None, SERIES, NOW)
    assert v and v.value_minor == 1_500_000_00 and v.as_of == NOW and "estimate" in v.note
    mid, _ = p.value_property(
        {"index": "dubai", "base_value_minor": 600_000_00, "base_date": "2024-03-01"}, None, None, SERIES, NOW
    )
    assert mid and mid.value_minor == 750_000_00  # 600k x 150/120
    acquired, _ = p.value_property({"index": "dubai"}, 1_000_000_00, date(2022, 1, 1), SERIES, NOW)
    assert acquired and acquired.value_minor == 1_500_000_00


def test_property_index_needs_everything():
    cases = [
        ({}, 100, date(2022, 1, 1), SERIES, "metadata.index"),
        ({"index": "d"}, None, date(2022, 1, 1), SERIES, "base value"),
        ({"index": "d"}, 100, None, SERIES, "base value"),
        ({"index": "d"}, 100, date(2022, 1, 1), [], "No index data"),
        ({"index": "d"}, 100, date(2010, 1, 1), SERIES, "no value near"),
    ]
    for meta, cost, acquired, series, fragment in cases:
        v, why = p.value_property(meta, cost, acquired, series, NOW)
        assert v is None and fragment in why, (meta, why)


def eff(**over):
    base = {
        "source": "market", "manual_minor": 900, "valued_at": NOW - timedelta(days=30), "market_minor": 1_000,
        "market_at": NOW - timedelta(hours=2), "note": "spot", "cost_basis_minor": 800, "now": NOW,
    }  # fmt: skip
    return p.effective_value(**{**base, **over})


def test_effective_value_prefers_fresh_market_then_falls_back_gracefully():
    fresh = eff()
    assert (fresh.value_minor, fresh.basis, fresh.note) == (1_000, "market", "spot")
    stale = eff(market_at=NOW - timedelta(days=4))
    assert stale.value_minor == 900 and stale.basis == "manual" and "stale" in (stale.note or "")
    assert stale.last_updated == NOW - timedelta(days=30)
    never = eff(market_minor=None, market_at=None, note="Add purity")
    assert never.value_minor == 900 and "Add purity" in (never.note or "")
    assert eff(market_minor=None, market_at=None, manual_minor=None).basis == "cost_basis"
    assert (
        eff(market_minor=None, market_at=None, manual_minor=None, cost_basis_minor=None).value_minor is None
    )
    manual = eff(source="manual")
    assert (manual.value_minor, manual.basis, manual.note) == (900, "manual", None)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_gold_api_provider_parses_and_fails_cleanly():
    ok = GoldApiProvider(client(lambda r: httpx.Response(200, json={"price": 2650.5, "symbol": "XAU"})))
    assert await ok.usd_per_troy_oz() == D("2650.5")
    for resp in (
        httpx.Response(500),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, json={"price": "x"}),
        httpx.Response(200, json={"price": -4}),
    ):
        with pytest.raises(ProviderError):
            await GoldApiProvider(client(lambda r, resp=resp: resp)).usd_per_troy_oz()

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError):
        await GoldApiProvider(client(boom)).usd_per_troy_oz()


async def test_fx_providers():
    body = {"result": "success", "rates": {"USD": 1, "AED": 3.6725, "EUR": 0.5, "INR": 83.0}}
    fx = OpenErApiFxProvider(client(lambda r: httpx.Response(200, json=body)))
    rates = await fx.aed_per_unit({"USD", "EUR", "AED", "XXX"})
    assert rates == {"USD": D("3.6725"), "EUR": D("7.345")}  # AED itself and unknown codes are skipped
    with pytest.raises(ProviderError):
        await OpenErApiFxProvider(
            client(lambda r: httpx.Response(200, json={"result": "error"}))
        ).aed_per_unit({"USD"})
    assert await PegFxProvider().aed_per_unit({"USD", "EUR"}) == {"USD": D("3.6725")}


async def test_static_property_index_sorts_and_validates():
    prov = StaticPropertyIndex({"dubai": {"2026-01-01": 150, "2022-01-01": 100}})
    assert (await prov.series())["dubai"] == [(date(2022, 1, 1), D(100)), (date(2026, 1, 1), D(150))]
    with pytest.raises(ProviderError):
        await StaticPropertyIndex({"x": {"2026-01-01": 0}}).series()

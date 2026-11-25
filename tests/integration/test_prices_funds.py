"""Price snapshot (TTL, stale-while-error, history), crypto/stock valuation, mutual funds and CAS import."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, update

from app.models.market import MarketQuote
from app.models.portfolio import FundHolding
from app.services.valuations.providers import Providers
from app.services.valuations.service import refresh_all
from tests.fake_markets import FakeCrypto, FakeFunds, FakeFx, FakeMetal, FakeStocks
from tests.statement_fixtures import render_pdf


@pytest.fixture(autouse=True)
async def clean_quotes(sessionmaker_):
    async with sessionmaker_() as s:
        await s.execute(delete(MarketQuote))
        await s.commit()
    yield
    async with sessionmaker_() as s:
        await s.execute(delete(MarketQuote))
        await s.commit()


def full_providers(**over):
    base = {
        "gold": FakeMetal("2000"), "silver": FakeMetal("25"), "fx": FakeFx(), "crypto": FakeCrypto(),
        "stocks": FakeStocks(), "funds": FakeFunds(),
    }  # fmt: skip
    return Providers(**{**base, **over})


async def test_snapshot_refreshes_on_request_then_serves_from_cache(new_user, price_providers):
    api = new_user()
    gold = FakeMetal("2000")
    price_providers.gold, price_providers.silver = gold, FakeMetal("25")
    price_providers.fx, price_providers.crypto = FakeFx(), FakeCrypto()
    r = await api.get("/v1/prices/snapshot")
    assert r.status_code == 200 and "max-age=900" in r.headers["cache-control"]
    s = r.json()
    assert s["status"] == "live" and s["ttl_seconds"] == 900 and gold.calls == 1
    g = s["gold"]
    assert float(g["per_oz"]["usd"]) == 2000 and float(g["per_oz"]["aed"]) == 7345
    assert set(g["per_gram"]) == {"24k", "22k", "21k", "18k"}
    assert float(g["per_gram"]["22k"]["aed"]) == 216.47 and float(g["per_gram"]["24k"]["usd"]) == 64.30
    assert set(s["silver"]["per_gram"]) == {"pure"} and float(s["silver"]["per_oz"]["usd"]) == 25
    assert {c["symbol"] for c in s["crypto"]} == {"bitcoin", "ethereum"}
    assert float(next(c for c in s["crypto"] if c["symbol"] == "bitcoin")["aed"]) == 220350
    assert any(f["currency"] == "USD" and f["status"] == "live" for f in s["fx"])
    await api.get("/v1/prices/snapshot")
    assert gold.calls == 1  # inside the TTL: no second fetch


async def test_snapshot_serves_stale_data_when_sources_fail(new_user, price_providers, sessionmaker_):
    api = new_user()
    async with sessionmaker_() as s:
        await refresh_all(s, full_providers())
        await s.execute(update(MarketQuote).values(fetched_at=datetime.now(UTC) - timedelta(hours=2)))
        await s.commit()
    price_providers.gold, price_providers.fx = FakeMetal(fail=True), FakeFx(fail=True)
    s = (await api.get("/v1/prices/snapshot")).json()
    assert (
        s["status"] == "stale" and float(s["gold"]["per_oz"]["usd"]) == 2000
    )  # last good value, not an error
    assert s["gold"]["status"] == "stale" and s["gold"]["last_updated"]


async def test_snapshot_with_nothing_cached_is_unavailable_not_an_error(new_user):
    s = (await new_user().get("/v1/prices/snapshot")).json()
    assert s["status"] == "unavailable" and s["gold"]["per_oz"] is None and s["gold"]["per_gram"] == {}
    assert s["fx"][0]["currency"] == "USD" and s["fx"][0]["status"] == "peg"


async def test_history_from_cache_with_provider_backfill(new_user, price_providers, sessionmaker_):
    api = new_user()
    async with sessionmaker_() as s:
        await refresh_all(s, full_providers())
    gold = (await api.get("/v1/prices/history?symbol=gold&days=30")).json()
    assert (
        len(gold["points"]) == 1 and "No free history" in gold["note"] and gold["unit"] == "USD per troy oz"
    )
    price_providers.crypto = FakeCrypto()
    c = (await api.get("/v1/prices/history?symbol=crypto:bitcoin&days=30")).json()
    assert len(c["points"]) >= 10 and c["source"] == "fake-crypto"
    assert (await api.get("/v1/prices/history?symbol=nonsense")).status_code == 404
    price_providers.crypto = FakeCrypto(fail=True)
    down = (await api.get("/v1/prices/history?symbol=crypto:dogecoin&days=30")).json()
    assert down["points"] == [] and "unavailable" in down["note"]


async def test_crypto_and_stock_assets_are_valued_live(new_user, sessionmaker_):
    api = new_user()
    btc = (
        await api.post(
            "/v1/assets",
            {
                "kind": "crypto",
                "name": "BTC",
                "quantity": "0.5",
                "manual_value_minor": 1_000,
                "valuation_source": "market",
                "meta": {"coingecko_id": "bitcoin"},
            },
        )
    ).json()
    aapl = (
        await api.post(
            "/v1/assets",
            {
                "kind": "stock",
                "name": "Apple",
                "quantity": "10",
                "manual_value_minor": 1_000,
                "valuation_source": "market",
                "meta": {"ticker": "AAPL.US"},
            },
        )
    ).json()
    assert btc["value_basis"] == "manual" and "No price cached" in btc["valuation_note"]
    async with sessionmaker_() as s:
        rep = await refresh_all(s, full_providers())
    assert rep.crypto == "ok" and rep.stocks == "ok" and rep.silver == "ok"
    b = (await api.get(f"/v1/assets/{btc['id']}")).json()
    assert b["value_basis"] == "market" and b["current_value_minor"] == 11_017_500  # 0.5 x 60000 USD x 3.6725
    a = (await api.get(f"/v1/assets/{aapl['id']}")).json()
    assert a["current_value_minor"] == 734_500 * 1  # 10 x 200 USD x 3.6725 = AED 7,345
    hist = (await api.get(f"/v1/assets/{btc['id']}/valuation-history")).json()
    assert (
        len(hist["points"]) == 1
        and hist["points"][0]["value_minor"] == 11_017_500
        and hist["change_minor"] is None
    )


async def test_manual_fund_nav_units_and_net_worth(new_user):
    api = new_user()
    h = (
        await api.post(
            "/v1/funds/holdings",
            {
                "kind": "manual",
                "name": "Gulf Equity Fund",
                "units": "100",
                "avg_cost_per_unit": "9.5",
                "manual_nav": "10.25",
            },
        )
    ).json()
    assert h["currency"] == "AED" and h["nav"]["basis"] == "manual" and h["value_minor"] == 102_500
    assert h["cost_minor"] == 95_000 and h["gain_minor"] == 7_500 and h["gain_pct"] == 7.89
    h = (await api.post(f"/v1/funds/holdings/{h['id']}/nav", {"nav": "11"})).json()
    assert h["value_aed_minor"] == 110_000 and h["nav"]["value"] == "11.000000"
    buy = await api.post(
        f"/v1/funds/holdings/{h['id']}/transactions",
        {"kind": "purchase", "txn_date": "2026-03-01", "units": "50", "nav": "10"},
    )
    assert buy.status_code == 201 and buy.json()["amount_minor"] == 50_000
    got = (await api.get(f"/v1/funds/holdings/{h['id']}")).json()
    assert got["units"] == "150.000000" and got["avg_cost_per_unit"] == "9.666667"
    sell = await api.post(
        f"/v1/funds/holdings/{h['id']}/transactions",
        {"kind": "redemption", "txn_date": "2026-03-02", "units": "40", "nav": "11"},
    )
    assert sell.json()["units"] == "-40.000000" and sell.json()["amount_minor"] == -44_000
    too_much = await api.post(
        f"/v1/funds/holdings/{h['id']}/transactions",
        {"kind": "redemption", "txn_date": "2026-03-03", "units": "1000", "nav": "1"},
    )
    assert too_much.status_code == 422
    assert len((await api.get(f"/v1/funds/holdings/{h['id']}/transactions")).json()) == 2
    nw = (await api.get("/v1/net-worth")).json()
    assert nw["assets_by_kind"]["fund"] == 121_000  # 110 units x AED 11
    summary = (await api.get("/v1/funds/holdings")).json()
    assert summary["total_value_aed_minor"] == 121_000 and summary["unconverted"] == []
    assert (
        await api.post("/v1/funds/holdings", {"kind": "india_mf", "name": "X", "currency": "AED"})
    ).status_code == 422
    assert (await new_user().get(f"/v1/funds/holdings/{h['id']}")).status_code == 404
    assert (await api.delete(f"/v1/funds/holdings/{h['id']}")).status_code == 204
    assert (await api.get("/v1/funds/holdings")).json()["holdings"] == []


async def test_indian_fund_search_nav_and_inr_conversion(new_user, price_providers, sessionmaker_):
    api = new_user()
    price_providers.funds = FakeFunds()
    found = (await api.get("/v1/funds/search?q=flexi cap")).json()
    assert found == [{"scheme_code": "119551", "name": "Example Flexi Cap Fund - Direct Plan - Growth"}]
    latest = (await api.get("/v1/funds/schemes/119551/latest")).json()
    assert latest["nav"] == "100.5000" or float(latest["nav"]) == 100.5
    h = (
        await api.post(
            "/v1/funds/holdings",
            {
                "kind": "india_mf",
                "name": "Flexi",
                "scheme_code": "119551",
                "units": "200",
                "avg_cost_per_unit": "90",
                "sip_amount_minor": 500_000,
                "sip_day": 5,
            },
        )
    ).json()
    assert h["currency"] == "INR" and h["sip_day"] == 5
    assert h["nav"]["basis"] == "market"  # the lookup above cached the NAV
    assert h["value_minor"] == 2_010_000 and h["cost_minor"] == 1_800_000 and h["gain_pct"] == 11.67
    assert h["value_aed_minor"] is None and "No INR/AED rate" in h["note"]  # no FX cached yet: never guessed
    async with sessionmaker_() as s:
        await refresh_all(s, full_providers())  # caches INR (the holding's currency) and fresh NAVs
    h = (await api.get(f"/v1/funds/holdings/{h['id']}")).json()
    assert float(h["fx_rate_to_aed"]) == 0.044 and h["value_aed_minor"] == 88_440  # INR 20,100 x 0.044
    hist = (await api.get(f"/v1/funds/holdings/{h['id']}/history?days=30")).json()
    assert hist["unit"] == "NAV (INR)" and len(hist["points"]) >= 5
    nw = (await api.get("/v1/net-worth")).json()
    assert nw["assets_by_kind"]["fund"] == 88_440 and nw["unconverted_currencies"] == []
    # failure: search degrades to a clear error, nothing crashes
    price_providers.funds = FakeFunds(fail=True)
    assert (await api.get("/v1/funds/search?q=flexi")).status_code == 422
    price_providers.funds = None
    assert (await api.get("/v1/funds/search?q=flexi")).json()["error"]["code"] == "funds_disabled"


CAS_LINES = [
    "Consolidated Account Statement",
    "Folio No: 12345678 / 90",
    "Example Mutual Fund",
    "EX12-Example Flexi Cap Fund - Direct Plan - Growth ISIN: INF000A01AB1(Advisor: DIRECT) Registrar : CAMS",
    "Opening Unit Balance: 0.000",
    "15-Jan-2024 Purchase 5,000.00 25.123 199.0160 25.123",
    "15-Feb-2024 SIP Purchase 5,000.00 24.800 201.6129 49.923",
    "10-Mar-2024 Redemption (2,000.00) (9.500) 210.5263 40.423",
    "Closing Unit Balance: 40.423",
    "NAV on 31-Mar-2024: INR 215.1000 Total Cost Value: 8,000.00 Market Value on 31-Mar-2024: INR 8,695.19",
]


async def test_cas_import_with_password_dedupes_and_resolves_schemes(new_user, price_providers):
    api = new_user()
    price_providers.funds = FakeFunds()
    pdf = render_pdf(CAS_LINES, password="ABCDE1234F01011990")
    no_pw = await api.upload("/v1/funds/cas/import", pdf, "cas.pdf")
    assert no_pw.status_code == 422 and no_pw.json()["error"]["code"] == "password_required"
    bad = await api.upload("/v1/funds/cas/import", pdf, "cas.pdf", {"password": "wrong"})
    assert bad.json()["error"]["code"] == "password_invalid"
    ok = await api.upload("/v1/funds/cas/import", pdf, "cas.pdf", {"password": "ABCDE1234F01011990"})
    assert ok.status_code == 200, ok.text
    out = ok.json()
    assert (out["holdings_created"], out["holdings_updated"], out["transactions_added"]) == (1, 0, 3)
    h = out["holdings"][0]
    assert h["name"] == "Example Flexi Cap Fund - Direct Plan - Growth" and h["folio"] == "12345678/90"
    assert h["units"] == "40.423000" and h["scheme_code"] == "119551" and out["unresolved_schemes"] == []
    assert (
        h["nav"]["basis"] == "manual" and h["nav"]["value"] == "215.100000"
    )  # CAS NAV until a live one arrives
    assert h["cost_minor"] == 800_000 and h["value_minor"] == 869_499
    again = (
        await api.upload("/v1/funds/cas/import", pdf, "cas.pdf", {"password": "ABCDE1234F01011990"})
    ).json()
    assert (again["holdings_created"], again["holdings_updated"], again["transactions_added"]) == (0, 1, 0)
    assert again["transactions_skipped_duplicate"] == 3
    assert len((await api.get(f"/v1/funds/holdings/{h['id']}/transactions")).json()) == 3
    # an unencrypted PDF needs no password; garbage is rejected clearly; other users never see it
    plain = render_pdf(["hello world"])
    assert (await api.upload("/v1/funds/cas/import", plain, "x.pdf")).json()["error"]["code"] == "not_a_cas"
    assert (await api.upload("/v1/funds/cas/import", b"not a pdf", "x.pdf")).status_code == 422
    assert (await new_user().get("/v1/funds/holdings")).json()["holdings"] == []


async def test_cas_import_leaves_unresolved_schemes_for_manual_codes(
    new_user, price_providers, sessionmaker_
):
    api = new_user()
    price_providers.funds = FakeFunds(schemes=[])
    pdf = render_pdf(CAS_LINES)
    out = (await api.upload("/v1/funds/cas/import", pdf, "cas.pdf")).json()
    assert out["unresolved_schemes"] == ["Example Flexi Cap Fund - Direct Plan - Growth"]
    assert out["holdings"][0]["scheme_code"] is None and out["holdings"][0]["nav"]["basis"] == "manual"
    fixed = (
        await api.patch(f"/v1/funds/holdings/{out['holdings'][0]['id']}", {"scheme_code": "119551"})
    ).json()
    assert fixed["scheme_code"] == "119551"
    async with sessionmaker_() as s:
        rep = await refresh_all(s, full_providers())
    assert rep.funds in ("ok", "partial")
    assert (await api.get(f"/v1/funds/holdings/{fixed['id']}")).json()["nav"]["basis"] == "market"
    _ = (FundHolding, Decimal)


async def test_stock_quotes_by_ticker_cache_and_stale(new_user, price_providers):
    api = new_user()
    stocks = FakeStocks({"voo.us": Decimal("500.25"), "aapl.us": Decimal("200")})
    price_providers.stocks = stocks
    r = await api.get("/v1/prices/stocks", params={"tickers": "VOO, aapl.us,voo,bad ticker!,zzzz"})
    assert r.status_code == 200 and "max-age=900" in r.headers["cache-control"]
    q = {x["ticker"]: x for x in r.json()["quotes"]}
    assert list(q) == ["voo.us", "aapl.us", "zzzz.us"]  # normalised, de-duplicated, junk dropped
    assert float(q["voo.us"]["usd"]) == 500.25 and q["voo.us"]["status"] == "live"
    assert q["zzzz.us"]["usd"] is None and q["zzzz.us"]["status"] == "unavailable"
    price_providers.stocks = FakeStocks(fail=True)
    r = await api.get("/v1/prices/stocks", params={"tickers": "voo"})
    assert float(r.json()["quotes"][0]["usd"]) == 500.25  # served from cache, source not needed


def test_normalise_tickers():
    from app.services.valuations.prices import normalise_tickers

    assert normalise_tickers("VOO,brk-b, aapl.us,,;;") == ["voo.us", "brk-b.us", "aapl.us"]
    assert len(normalise_tickers(",".join(f"t{i}" for i in range(40)))) == 25

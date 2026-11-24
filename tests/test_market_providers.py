"""HTTP providers against a mock transport, and the CAS parser on synthetic text (no network, no real data)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.models.portfolio import FundTxnKind
from app.services.cas import classify, parse_cas
from app.services.valuations.providers import CoinGeckoProvider, MfApiProvider, ProviderError, StooqProvider

D = Decimal


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_coingecko_prices_and_history():
    def handler(req):
        if req.url.path.endswith("/simple/price"):
            assert (
                req.url.params["ids"] == "bitcoin,ethereum,ghost" and req.url.params["vs_currencies"] == "usd"
            )
            return httpx.Response(200, json={"bitcoin": {"usd": 60000.5}, "ethereum": {"usd": 3000}})
        return httpx.Response(200, json={"prices": [[1_700_000_000_000, 100], [1_700_086_400_000, 110]]})

    p = CoinGeckoProvider(client(handler))
    assert await p.usd_prices({"bitcoin", "ethereum", "ghost"}) == {
        "bitcoin": D("60000.5"),
        "ethereum": D(3000),
    }
    assert await p.usd_prices(set()) == {}
    hist = await p.history("bitcoin", 7)
    assert [v for _, v in hist] == [D(100), D(110)] and hist[0][0] == date(2023, 11, 14)
    with pytest.raises(ProviderError):
        await CoinGeckoProvider(client(lambda r: httpx.Response(429))).usd_prices({"bitcoin"})


async def test_stooq_csv_skips_unknown_tickers():
    csv = "Symbol,Date,Close\nAAPL.US,2026-06-12,201.5\nZZZZ.US,N/D,N/D\n"
    p = StooqProvider(client(lambda r: httpx.Response(200, text=csv)))
    assert await p.usd_prices({"aapl.us", "zzzz.us"}) == {"aapl.us": D("201.5")}
    hist_csv = (
        "Date,Open,High,Low,Close,Volume\n2026-06-10,1,1,1,190,5\n2026-06-11,1,1,1,195,5\nbad,1,1,1,x,5\n"
    )
    h = StooqProvider(client(lambda r: httpx.Response(200, text=hist_csv)))
    assert await h.history("aapl.us", 1) == [(date(2026, 6, 11), D(195))]
    with pytest.raises(ProviderError):
        await StooqProvider(client(lambda r: httpx.Response(503))).usd_prices({"aapl.us"})


async def test_mfapi_search_latest_history():
    def handler(req):
        path = req.url.path
        if path == "/mf/search":
            return httpx.Response(200, json=[{"schemeCode": 119551, "schemeName": "Example Fund - Growth"}])
        if path.endswith("/latest"):
            return httpx.Response(
                200,
                json={
                    "meta": {"scheme_name": "Example Fund"},
                    "data": [{"date": "12-06-2026", "nav": "101.2345"}],
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"date": "12-06-2026", "nav": "101.2"}, {"date": "11-06-2026", "nav": "100.1"}]},
        )

    p = MfApiProvider(client(handler))
    assert [(s.code, s.name) for s in await p.search("example")] == [("119551", "Example Fund - Growth")]
    nav = await p.latest("119551")
    assert (nav.nav_date, nav.nav, nav.name) == (date(2026, 6, 12), D("101.2345"), "Example Fund")
    hist = await p.history("119551", 365)
    assert hist == [(date(2026, 6, 11), D("100.1")), (date(2026, 6, 12), D("101.2"))]
    for resp in (
        httpx.Response(500),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, text="oops"),
    ):
        with pytest.raises(ProviderError):
            await MfApiProvider(client(lambda r, resp=resp: resp)).latest("1")


CAS = """
Consolidated Account Statement
PAN: ABCDE1234F
Folio No: 12345678 / 90
Example Mutual Fund
EX12-Example Flexi Cap Fund - Direct Plan - Growth ISIN: INF000A01AB1(Advisor: DIRECT) Registrar : CAMS
Opening Unit Balance: 0.000
15-Jan-2024 Purchase 5,000.00 25.123 199.0160 25.123
15-Feb-2024 SIP Purchase 5,000.00 24.800 201.6129 49.923
*** Stamp Duty *** 0.25
10-Mar-2024 Redemption (2,000.00) (9.500) 210.5263 40.423
Closing Unit Balance: 40.423
NAV on 31-Mar-2024: INR 215.1000 Total Cost Value: 8,000.00 Market Value on 31-Mar-2024: INR 8,695.19

Folio No: 555 PAN: ABCDE1234F
Another Mutual Fund
AN01-Another Liquid Fund - Regular Plan ISIN: INF000B01CD2 Registrar : KFINTECH
11-Mar-2024 Switch In 1,000.00 1.000 1000.0000 1.000
unreadable 99-Foo-2024 line
Closing Unit Balance: 1.000
"""


def test_cas_parser_reads_schemes_transactions_and_balances():
    r = parse_cas(CAS)
    assert [h.name for h in r.holdings] == [
        "Example Flexi Cap Fund - Direct Plan - Growth",
        "Another Liquid Fund - Regular Plan",
    ]
    h = r.holdings[0]
    assert (h.folio, h.isin, h.amc) == ("12345678/90", "INF000A01AB1", "Example Mutual Fund")
    assert h.closing_units == D("40.423") and h.nav == D("215.1000") and h.nav_date == date(2024, 3, 31)
    assert h.total_cost_minor == 800_000 and h.avg_cost_per_unit == D("197.9071")
    kinds = [(t.kind, t.units, t.amount_minor) for t in h.transactions]
    assert kinds == [
        (FundTxnKind.purchase, D("25.123"), 500_000),
        (FundTxnKind.sip, D("24.800"), 500_000),
        (FundTxnKind.redemption, D("-9.500"), -200_000),
    ]
    other = r.holdings[1]
    assert (
        other.folio == "555"
        and other.transactions[0].kind is FundTxnKind.switch_in
        and other.avg_cost_per_unit is None
    )
    assert len({t.dedupe_hash for t in h.transactions}) == 3


def test_cas_parser_without_closing_balance_and_garbage():
    text = CAS.split("Folio No: 555")[0].replace("Closing Unit Balance: 40.423", "")
    r = parse_cas(text)
    assert r.holdings[0].units == D("40.423") and any("No closing balance" in w for w in r.warnings)
    g = parse_cas("hello world\nnothing here")
    assert g.holdings == [] and "No schemes recognised" in g.warnings[0]


def test_classify_descriptions():
    assert classify("Systematic Investment Purchase") is FundTxnKind.sip
    assert classify("Switch Out - to Foo") is FundTxnKind.switch_out
    assert classify("Redemption") is FundTxnKind.redemption
    assert classify("Dividend Payout") is FundTxnKind.dividend
    assert classify("Mystery") is FundTxnKind.other

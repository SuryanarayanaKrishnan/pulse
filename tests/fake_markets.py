"""Fake market-data providers shared by the tests (no network)."""

from datetime import date, timedelta
from decimal import Decimal

from app.services.valuations.providers import FundNav, FundScheme, ProviderError


class FakeMetal:
    def __init__(self, price="2000", fail=False, name="fake-metal"):
        self.price, self.fail, self.name, self.calls = Decimal(price), fail, name, 0

    async def usd_per_troy_oz(self):
        self.calls += 1
        if self.fail:
            raise ProviderError("metal down")
        return self.price


class FakeFx:
    name = "fake-fx"

    def __init__(self, rates=None, fail=False):
        self.rates = rates or {"USD": Decimal("3.6725"), "EUR": Decimal("4"), "INR": Decimal("0.044")}
        self.fail = fail

    async def aed_per_unit(self, currencies):
        if self.fail:
            raise ProviderError("fx down")
        return {c: r for c, r in self.rates.items() if c in currencies}


class FakeCrypto:
    name = "fake-crypto"

    def __init__(self, prices=None, fail=False):
        self.prices, self.fail = prices or {"bitcoin": Decimal("60000"), "ethereum": Decimal("3000")}, fail

    async def usd_prices(self, ids):
        if self.fail:
            raise ProviderError("crypto down")
        return {i: p for i, p in self.prices.items() if i in ids}

    async def history(self, coin_id, days):
        if self.fail:
            raise ProviderError("crypto history down")
        today = date.today()
        return [(today - timedelta(days=i), Decimal(100 + i)) for i in range(min(days, 10), 0, -1)]


class FakeStocks:
    name = "fake-stooq"

    def __init__(self, prices=None, fail=False):
        self.prices, self.fail = prices or {"aapl.us": Decimal("200")}, fail

    async def usd_prices(self, tickers):
        if self.fail:
            raise ProviderError("stocks down")
        return {t: p for t, p in self.prices.items() if t in tickers}

    async def history(self, ticker, days):
        today = date.today()
        return [(today - timedelta(days=i), Decimal(190 + i)) for i in range(min(days, 8), 0, -1)]


class FakeFunds:
    name = "fake-mfapi"

    def __init__(self, navs=None, schemes=None, fail=False):
        self.navs = navs or {"119551": Decimal("100.5")}
        self.schemes = (
            schemes
            if schemes is not None
            else [
                FundScheme("119551", "Example Flexi Cap Fund - Direct Plan - Growth"),
                FundScheme("100001", "Example Liquid Fund - Regular"),
            ]
        )
        self.fail = fail

    async def search(self, query):
        if self.fail:
            raise ProviderError("mfapi down")
        q = query.lower()
        return [s for s in self.schemes if all(w in s.name.lower() for w in q.split())]

    async def latest(self, code):
        if self.fail or code not in self.navs:
            raise ProviderError(f"no NAV for {code}")
        return FundNav(code, "Example", date.today(), self.navs[code])

    async def history(self, code, days):
        if self.fail:
            raise ProviderError("mfapi down")
        today = date.today()
        return [
            (today - timedelta(days=i), self.navs.get(code, Decimal(100)) - i)
            for i in range(min(days, 12), 0, -1)
        ]

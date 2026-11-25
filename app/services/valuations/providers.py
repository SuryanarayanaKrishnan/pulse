"""Market-data sources behind small interfaces. Everything here may fail; callers always fall back.

The HTTP endpoints are free, key-less public services written from their documentation; they are NOT
exercised by the test suite (which uses fakes) and should be checked against the live service before being
relied on in production. Swap them by implementing the protocols and returning them from ``get_providers``.
"""

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
import structlog

from app.core.config import get_settings
from app.services.valuations.pricing import AED_PER_USD_PEG

log = structlog.get_logger(__name__)


class ProviderError(Exception):
    """A source was unreachable or returned something unusable."""


class GoldProvider(Protocol):
    name: str

    async def usd_per_troy_oz(self) -> Decimal: ...


class FxProvider(Protocol):
    name: str

    async def aed_per_unit(self, currencies: set[str]) -> dict[str, Decimal]:
        """AED per 1 unit of each requested currency (those it cannot price are omitted)."""
        ...


class PropertyIndexProvider(Protocol):
    name: str

    async def series(self) -> dict[str, list[tuple[date, Decimal]]]: ...


def _dec(value: object) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProviderError(f"not a number: {value!r}") from exc
    if not d.is_finite() or d <= 0:
        raise ProviderError(f"not a positive number: {value!r}")
    return d


@dataclass(slots=True)
class GoldApiProvider:
    """gold-api.com: GET /price/<XAU|XAG> -> {"price": <USD per troy oz>, ...} (free, no key)."""

    client: httpx.AsyncClient
    url: str = "https://api.gold-api.com/price/XAU"
    name: str = "gold-api.com"

    async def usd_per_troy_oz(self) -> Decimal:
        try:
            r = await self.client.get(self.url)
            r.raise_for_status()
            return _dec(r.json()["price"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"gold price unavailable: {exc}") from exc


@dataclass(slots=True)
class OpenErApiFxProvider:
    """open.er-api.com: GET /v6/latest/USD -> {"result": "success", "rates": {"AED": ...}} (no key)."""

    client: httpx.AsyncClient
    url: str = "https://open.er-api.com/v6/latest/USD"
    name: str = "open.er-api.com"

    async def aed_per_unit(self, currencies: set[str]) -> dict[str, Decimal]:
        try:
            r = await self.client.get(self.url)
            r.raise_for_status()
            body = r.json()
            if body.get("result") != "success":
                raise ProviderError("fx service reported failure")
            rates = body["rates"]
            aed = _dec(rates["AED"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"fx rates unavailable: {exc}") from exc
        out: dict[str, Decimal] = {}
        for ccy in currencies:
            if ccy == "AED":
                continue
            if ccy in rates:
                out[ccy] = aed / _dec(rates[ccy])
        return out


@dataclass(slots=True)
class PegFxProvider:
    """AED is fixed to USD (3.6725); no network needed. Only USD."""

    name: str = "aed-usd-peg"

    async def aed_per_unit(self, currencies: set[str]) -> dict[str, Decimal]:
        return {"USD": AED_PER_USD_PEG} if "USD" in currencies else {}


@dataclass(slots=True)
class StaticPropertyIndex:
    """Index levels supplied by configuration (no free property API is wired in)."""

    data: dict[str, dict[str, float]]
    name: str = "configured"

    async def series(self) -> dict[str, list[tuple[date, Decimal]]]:
        out: dict[str, list[tuple[date, Decimal]]] = {}
        for key, points in self.data.items():
            out[key] = sorted((date.fromisoformat(d), _dec(v)) for d, v in points.items())
        return out


class CryptoProvider(Protocol):
    name: str

    async def usd_prices(self, ids: set[str]) -> dict[str, Decimal]: ...

    async def history(self, coin_id: str, days: int) -> list[tuple[date, Decimal]]: ...


class StockProvider(Protocol):
    name: str

    async def usd_prices(self, tickers: set[str]) -> dict[str, Decimal]: ...

    async def history(self, ticker: str, days: int) -> list[tuple[date, Decimal]]: ...


@dataclass(frozen=True, slots=True)
class FundScheme:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class FundNav:
    code: str
    name: str | None
    nav_date: date
    nav: Decimal


class FundProvider(Protocol):
    name: str

    async def search(self, query: str) -> list[FundScheme]: ...

    async def latest(self, code: str) -> FundNav: ...

    async def history(self, code: str, days: int) -> list[tuple[date, Decimal]]: ...


@dataclass(slots=True)
class CoinGeckoProvider:
    """CoinGecko public API (free, no key, rate limited): /simple/price and /coins/{id}/market_chart."""

    client: httpx.AsyncClient
    base: str = "https://api.coingecko.com/api/v3"
    name: str = "coingecko"

    async def usd_prices(self, ids: set[str]) -> dict[str, Decimal]:
        if not ids:
            return {}
        try:
            r = await self.client.get(
                f"{self.base}/simple/price", params={"ids": ",".join(sorted(ids)), "vs_currencies": "usd"}
            )
            r.raise_for_status()
            body = r.json()
            return {i: _dec(body[i]["usd"]) for i in ids if i in body and "usd" in body[i]}
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"crypto prices unavailable: {exc}") from exc

    async def history(self, coin_id: str, days: int) -> list[tuple[date, Decimal]]:
        try:
            r = await self.client.get(
                f"{self.base}/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
            )
            r.raise_for_status()
            out: dict[date, Decimal] = {}
            for ms, price in r.json()["prices"]:
                out[datetime.fromtimestamp(ms / 1000, tz=UTC).date()] = _dec(price)
            return sorted(out.items())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"crypto history unavailable: {exc}") from exc


@dataclass(slots=True)
class StooqProvider:
    """Stooq CSV quotes (free, no key; US tickers as 'aapl.us', USD). Stooq's terms allow free personal
    use of its downloads; keep volumes low (the cache does) and swap this class if that ever changes.
    Unofficial scraping endpoints (e.g. Yahoo's) are deliberately NOT used."""

    client: httpx.AsyncClient
    base: str = "https://stooq.com"
    name: str = "stooq.com"

    async def usd_prices(self, tickers: set[str]) -> dict[str, Decimal]:
        if not tickers:
            return {}
        out: dict[str, Decimal] = {}
        try:
            r = await self.client.get(
                f"{self.base}/q/l/", params={"s": "+".join(sorted(tickers)), "f": "sd2c", "h": "", "e": "csv"}
            )
            r.raise_for_status()
            for row in csv.DictReader(io.StringIO(r.text)):
                try:
                    out[row["Symbol"].lower()] = _dec(row["Close"])
                except (KeyError, ProviderError):
                    continue  # N/D for an unknown ticker
        except httpx.HTTPError as exc:
            raise ProviderError(f"stock prices unavailable: {exc}") from exc
        return out

    async def history(self, ticker: str, days: int) -> list[tuple[date, Decimal]]:
        try:
            r = await self.client.get(f"{self.base}/q/d/l/", params={"s": ticker, "i": "d"})
            r.raise_for_status()
            rows = []
            for row in csv.DictReader(io.StringIO(r.text)):
                try:
                    rows.append((date.fromisoformat(row["Date"]), _dec(row["Close"])))
                except (KeyError, ValueError, ProviderError):
                    continue
            return rows[-days:]
        except httpx.HTTPError as exc:
            raise ProviderError(f"stock history unavailable: {exc}") from exc


NSE_ARCHIVE = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"


@dataclass(slots=True)
class NseBhavcopyProvider:
    """NSE's own free end-of-day file (the daily 'bhavcopy'), one CSV for every listed equity, INR.
    No key; it is a static file, so one download answers any number of tickers. Tickers look like
    'reliance.ns'. If today's file is not out yet (weekend, before the evening publish) it steps back
    up to ``lookback`` days. Written from the file's documented layout; verify against the live file."""

    client: httpx.AsyncClient
    name: str = "nseindia.com"
    currency: str = "INR"
    lookback: int = 6

    async def _latest_rows(self) -> dict[str, Decimal]:
        day = datetime.now(UTC).date()
        for _ in range(self.lookback):
            if day.weekday() < 5:
                try:
                    r = await self.client.get(
                        NSE_ARCHIVE.format(d=day.strftime("%d%m%Y")),
                        headers={"User-Agent": "Mozilla/5.0 (compatible; PulseApp)"},
                    )
                except httpx.HTTPError as exc:
                    raise ProviderError(f"NSE file unavailable: {exc}") from exc
                if r.status_code == 200 and r.text.strip():
                    return parse_bhavcopy(r.text)
            day = date.fromordinal(day.toordinal() - 1)
        raise ProviderError("NSE file unavailable: no recent bhavcopy")

    async def usd_prices(self, tickers: set[str]) -> dict[str, Decimal]:
        """Named for the StockProvider protocol; values are INR here (see ``currency``)."""
        want = {t[: -len(".ns")].upper(): t for t in tickers if t.endswith(".ns")}
        if not want:
            return {}
        rows = await self._latest_rows()
        return {want[sym]: px for sym, px in rows.items() if sym in want}

    async def history(self, ticker: str, days: int) -> list[tuple[date, Decimal]]:
        return []


def parse_bhavcopy(text: str) -> dict[str, Decimal]:
    """SYMBOL -> CLOSE_PRICE for the cash equity series (EQ, BE, SM, ST). Header cells carry stray spaces."""
    out: dict[str, Decimal] = {}
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(reader, [])]
    try:
        i_sym, i_ser, i_close = header.index("SYMBOL"), header.index("SERIES"), header.index("CLOSE_PRICE")
    except ValueError:
        raise ProviderError("NSE file has an unexpected layout") from None
    for row in reader:
        if len(row) <= max(i_sym, i_ser, i_close) or row[i_ser].strip() not in {"EQ", "BE", "SM", "ST"}:
            continue
        try:
            px = _dec(row[i_close].strip())
        except ProviderError:
            continue
        out[row[i_sym].strip().upper()] = px
    return out


@dataclass(slots=True)
class RoutedStockProvider:
    """Sends '.ns' tickers to NSE and everything else to Stooq. ``currency_of`` says what a quote is in."""

    us: StockProvider
    nse: NseBhavcopyProvider
    name: str = "stooq.com + nseindia.com"

    @staticmethod
    def currency_of(ticker: str) -> str:
        return "INR" if ticker.endswith(".ns") else "USD"

    def source_of(self, ticker: str) -> str:
        return self.nse.name if ticker.endswith(".ns") else self.us.name

    async def usd_prices(self, tickers: set[str]) -> dict[str, Decimal]:
        ns = {t for t in tickers if t.endswith(".ns")}
        out: dict[str, Decimal] = {}
        errors: list[ProviderError] = []
        for prov, part in ((self.us, tickers - ns), (self.nse, ns)):
            if not part:
                continue
            try:
                out.update(await prov.usd_prices(part))
            except ProviderError as exc:
                errors.append(exc)
        if errors and not out:
            raise errors[0]
        return out

    async def history(self, ticker: str, days: int) -> list[tuple[date, Decimal]]:
        return await (self.nse if ticker.endswith(".ns") else self.us).history(ticker, days)


@dataclass(slots=True)
class MfApiProvider:
    """mfapi.in (free, no key): /mf/search?q=, /mf/{code}/latest, /mf/{code}. NAVs are in INR, dd-mm-yyyy."""

    client: httpx.AsyncClient
    base: str = "https://api.mfapi.in"
    name: str = "mfapi.in"

    async def _get(self, path: str, **params: str) -> object:
        try:
            r = await self.client.get(f"{self.base}{path}", params=params or None)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"mfapi unavailable: {exc}") from exc

    async def search(self, query: str) -> list[FundScheme]:
        body = await self._get("/mf/search", q=query)
        if not isinstance(body, list):
            raise ProviderError("unexpected search response")
        return [FundScheme(str(x["schemeCode"]), str(x["schemeName"])) for x in body if "schemeCode" in x][
            :50
        ]

    @staticmethod
    def _point(row: dict[str, str]) -> tuple[date, Decimal]:
        d, m, y = row["date"].split("-")
        return date(int(y), int(m), int(d)), _dec(row["nav"])

    async def latest(self, code: str) -> FundNav:
        body = await self._get(f"/mf/{code}/latest")
        try:
            assert isinstance(body, dict)
            d, nav = self._point(body["data"][0])
            return FundNav(code, (body.get("meta") or {}).get("scheme_name"), d, nav)
        except (KeyError, IndexError, ValueError, TypeError, AssertionError) as exc:
            raise ProviderError(f"unexpected NAV response for {code}") from exc

    async def history(self, code: str, days: int) -> list[tuple[date, Decimal]]:
        body = await self._get(f"/mf/{code}")
        try:
            assert isinstance(body, dict)
            pts = sorted(self._point(r) for r in body["data"])
        except (KeyError, ValueError, TypeError, AssertionError) as exc:
            raise ProviderError(f"unexpected NAV history for {code}") from exc
        return pts[-days:]


@dataclass(slots=True)
class Providers:
    gold: GoldProvider | None = None
    silver: GoldProvider | None = None
    fx: FxProvider | None = None
    property_index: PropertyIndexProvider | None = None
    crypto: CryptoProvider | None = None
    stocks: StockProvider | None = None
    funds: FundProvider | None = None


def get_providers(client: httpx.AsyncClient) -> Providers:
    s = get_settings()
    prop: PropertyIndexProvider | None = None
    if s.property_index_json.strip():
        try:
            prop = StaticPropertyIndex(json.loads(s.property_index_json))
        except (ValueError, TypeError):
            log.error("property_index_json_invalid")
    fx: FxProvider | None = None
    if s.fx_provider == "open_er_api":
        fx = OpenErApiFxProvider(client)
    elif s.fx_provider == "peg":
        fx = PegFxProvider()
    on = s.gold_provider == "goldapi"
    return Providers(
        gold=GoldApiProvider(client) if on else None,
        silver=GoldApiProvider(client, url="https://api.gold-api.com/price/XAG") if on else None,
        fx=fx,
        property_index=prop,
        crypto=CoinGeckoProvider(client) if s.crypto_provider == "coingecko" else None,
        stocks=(
            RoutedStockProvider(StooqProvider(client), NseBhavcopyProvider(client))
            if s.stock_provider == "stooq"
            else None
        ),
        funds=MfApiProvider(client) if s.fund_provider == "mfapi" else None,
    )

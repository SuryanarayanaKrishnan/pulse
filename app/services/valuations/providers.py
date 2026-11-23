"""Market-data sources behind small interfaces. Everything here may fail; callers always fall back.

The HTTP endpoints are free, key-less public services written from their documentation; they are NOT
exercised by the test suite (which uses fakes) and should be checked against the live service before being
relied on in production. Swap them by implementing the protocols and returning them from ``get_providers``.
"""

import json
from dataclasses import dataclass
from datetime import date
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
    """gold-api.com: GET /price/XAU -> {"price": <USD per troy oz>, ...} (no key)."""

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


@dataclass(slots=True)
class Providers:
    gold: GoldProvider | None = None
    fx: FxProvider | None = None
    property_index: PropertyIndexProvider | None = None


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
    return Providers(
        gold=GoldApiProvider(client) if s.gold_provider == "goldapi" else None, fx=fx, property_index=prop
    )

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.schemas.prices import HistoryOut, PriceSnapshotOut, StockQuotesOut
from app.services.valuations import prices
from app.services.valuations.providers import Providers, get_providers
from app.services.valuations.service import load_quote_book

router = APIRouter(prefix="/prices", tags=["prices"])


async def get_price_providers() -> AsyncIterator[Providers]:
    """Free public sources; overridden with fakes in tests."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(get_settings().valuation_timeout_seconds)) as client:
        yield get_providers(client)


ProvidersDep = Annotated[Providers, Depends(get_price_providers)]


@router.get("/snapshot", response_model=PriceSnapshotOut)
async def price_snapshot(
    user: CurrentUser, session: SessionDep, providers: ProvidersDep, response: Response
) -> PriceSnapshotOut:
    """One payload for the phone to cache: gold and silver (per gram and ounce, AED and USD, gold by karat),
    FX, crypto and stocks. Refreshed on request when older than the TTL; if a source is down the last
    good values are served and marked stale."""
    await prices.ensure_fresh(session, providers)
    snap = prices.snapshot(await load_quote_book(session))
    response.headers["Cache-Control"] = f"private, max-age={snap.ttl_seconds}"
    return snap


@router.get("/stocks", response_model=StockQuotesOut)
async def stock_prices(
    user: CurrentUser,
    session: SessionDep,
    providers: ProvidersDep,
    response: Response,
    tickers: Annotated[str, Query(description="Comma-separated US tickers, e.g. VOO,AAPL (max 25)")],
) -> StockQuotesOut:
    """Last close for the tickers a phone tracks (free Stooq source, cached for the TTL, stale on error).
    Only the tickers are sent: no amounts, quantities or user data."""
    out = await prices.stock_quotes(session, providers, prices.normalise_tickers(tickers))
    response.headers["Cache-Control"] = f"private, max-age={out.ttl_seconds}"
    return out


@router.get("/history", response_model=HistoryOut)
async def price_history(
    user: CurrentUser,
    session: SessionDep,
    providers: ProvidersDep,
    symbol: Annotated[
        str, Query(description="gold | silver | fx:EUR | crypto:bitcoin | stock:aapl.us | nav:<code>")
    ],
    days: Annotated[int, Query(ge=1, le=1825)] = 90,
) -> HistoryOut:
    out = await prices.history(session, providers, symbol, days)
    if out is None:
        raise NotFoundError("Unknown symbol")
    return out

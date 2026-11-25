"""The price snapshot the phone caches, on-demand refresh with stale-while-error, and chart history."""

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.market import MarketQuote
from app.schemas.prices import (
    FxOut,
    HistoryOut,
    HistoryPoint,
    MetalOut,
    MoneyPair,
    PriceSnapshotOut,
    StockQuoteOut,
    StockQuotesOut,
    SymbolPriceOut,
)
from app.services.valuations import pricing
from app.services.valuations.providers import ProviderError, Providers
from app.services.valuations.service import (
    GOLD,
    SILVER,
    QuoteBook,
    _upsert,
    crypto_symbol,
    fx_symbol,
    load_quote_book,
    nav_symbol,
    refresh_quotes,
    stock_symbol,
)

log = structlog.get_logger(__name__)
KARATS = (24, 22, 21, 18)
REFRESH_TIMEOUT_SECONDS = 8.0
Q2 = Decimal("0.01")


def status_for(fetched_at: datetime | None, now: datetime, ttl: int) -> str:
    if fetched_at is None:
        return "unavailable"
    return "live" if now - fetched_at <= timedelta(seconds=ttl) else "stale"


async def ensure_fresh(session: AsyncSession, providers: Providers) -> bool:
    """Refresh the cache when the reference (gold) quote is older than the TTL. Never raises: on any
    failure or timeout the stale cache is served. True when a refresh ran."""
    s = get_settings()
    if not s.prices_refresh_on_request or not (providers.gold or providers.fx or providers.crypto):
        return False
    book = await load_quote_book(session)
    now = datetime.now(UTC)
    ref = book.gold[1] if book.gold else None
    if ref is not None and now - ref <= timedelta(seconds=s.price_ttl_seconds):
        return False
    try:
        await asyncio.wait_for(refresh_quotes(session, providers, now), REFRESH_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - stale-while-error is the contract
        log.warning("price_refresh_on_request_failed", exc_info=True)
        await session.rollback()
        return False
    return True


def _metal(
    entry: tuple[Decimal, datetime, str] | None, aed_per_usd: Decimal, now: datetime, ttl: int, pure: bool
) -> MetalOut:
    if entry is None:
        return MetalOut(status="unavailable", per_oz=None, per_gram={}, last_updated=None, source=None)
    usd_oz, at, src = entry

    def pair(usd: Decimal) -> MoneyPair:
        return MoneyPair(aed=(usd * aed_per_usd).quantize(Q2), usd=usd.quantize(Q2))

    grams = {}
    purities = {"pure": Decimal(1)} if pure else {f"{k}k": Decimal(k) / 24 for k in KARATS}
    for label, frac in purities.items():
        usd_g = usd_oz / pricing.GRAMS_PER_TROY_OZ * frac
        grams[label] = pair(usd_g)
    return MetalOut(
        status=status_for(at, now, ttl), per_oz=pair(usd_oz), per_gram=grams, last_updated=at, source=src
    )


def snapshot(book: QuoteBook, now: datetime | None = None) -> PriceSnapshotOut:
    s = get_settings()
    now = now or datetime.now(UTC)
    ttl = s.price_ttl_seconds
    fx = [
        FxOut(
            currency=c, aed_per_unit=v[0], status=status_for(v[1], now, ttl), last_updated=v[1], source=v[2]
        )
        for c, v in sorted(book.fx.items())
    ]
    if "USD" not in book.fx:
        fx.insert(
            0,
            FxOut(
                currency="USD",
                aed_per_unit=pricing.AED_PER_USD_PEG,
                status="peg",
                last_updated=None,
                source="peg",
            ),
        )

    def priced(items: dict[str, tuple[Decimal, datetime, str]]) -> list[SymbolPriceOut]:
        return [
            SymbolPriceOut(
                symbol=k,
                usd=v[0],
                aed=(v[0] * book.aed_per_usd).quantize(Q2),
                status=status_for(v[1], now, ttl),
                last_updated=v[1],
                source=v[2],
            )
            for k, v in sorted(items.items())
        ]

    gold = _metal(book.gold, book.aed_per_usd, now, ttl, pure=False)
    return PriceSnapshotOut(
        generated_at=now,
        ttl_seconds=ttl,
        status=gold.status,
        gold=gold,
        silver=_metal(book.silver, book.aed_per_usd, now, ttl, pure=True),
        fx=fx,
        crypto=priced(book.crypto),
        stocks=priced(book.stocks),
        notes=[
            "Metal prices are spot: retail jewellery and bullion add making charges and a dealer spread.",
            "Anything marked stale is the last good value; sources are free public APIs that can fail.",
            "Stocks are US tickers priced in USD; a ticker appears once an asset references it.",
        ],
    )


# ---------------------------------------------------------------------------------------------
# Stock quotes by ticker (phone portfolio tracking)
# ---------------------------------------------------------------------------------------------
MAX_QUOTE_TICKERS = 25
_TICKER_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,11}$")


def normalise_tickers(raw: str) -> list[str]:
    """'VOO, aapl.us, reliance.ns' -> ['voo.us', 'aapl.us', 'reliance.ns']. A bare ticker gets '.us'.
    '.ns' (NSE) is priced from NSE's free file; '.bo', '.dfm' and '.adx' pass through but have no free
    source, so they come back 'unavailable' and the phone asks for a manual price.
    Invalid entries are dropped and duplicates collapse, order kept."""
    out: list[str] = []
    for part in raw.split(","):
        t = part.strip().lower()
        if not t or not _TICKER_RE.match(t):
            continue
        if "." not in t:
            t += ".us"
        if t not in out:
            out.append(t)
    return out[:MAX_QUOTE_TICKERS]


def _quote_currency(providers: Providers, ticker: str) -> str:
    of = getattr(providers.stocks, "currency_of", None)
    return of(ticker) if of else "USD"


def _cached_stock(book: QuoteBook, ticker: str) -> tuple[Decimal, datetime, str, str] | None:
    if ticker in book.stocks:
        p, at, src = book.stocks[ticker]
        return p, at, src, "USD"
    local = book.local_stocks.get(ticker)
    return local if local is not None else None


async def stock_quotes(session: AsyncSession, providers: Providers, tickers: list[str]) -> StockQuotesOut:
    """Last close for any ticker the phone asks about (US via Stooq, '.ns' via NSE), cached for the TTL.
    Stale-while-error: if the source is down the last good close is served marked stale; a ticker with
    no free source is 'unavailable' and the phone falls back to a manual price."""
    s = get_settings()
    now = datetime.now(UTC)
    ttl = s.price_ttl_seconds
    book = await load_quote_book(session)

    def fresh(t: str) -> bool:
        hit = _cached_stock(book, t)
        return hit is not None and now - hit[1] <= timedelta(seconds=ttl)

    need = [t for t in tickers if not fresh(t)]
    stocks = providers.stocks
    if need and stocks:
        try:
            got = await asyncio.wait_for(stocks.usd_prices(set(need)), REFRESH_TIMEOUT_SECONDS)
            day = now.date()
            source_of = getattr(stocks, "source_of", lambda _t: stocks.name)
            for t, price in got.items():
                ccy, src = _quote_currency(providers, t), source_of(t)
                await _upsert(session, stock_symbol(t, ccy), day, price, src, now)
                if ccy == "USD":
                    book.stocks[t] = (price, now, src)
                else:
                    book.local_stocks[t] = (price, now, src, ccy)
            await session.commit()
        except Exception:  # noqa: BLE001 - stale-while-error is the contract
            log.warning("stock_quotes_failed", exc_info=True)
            await session.rollback()
    out = []
    for t in tickers:
        hit = _cached_stock(book, t)
        if hit is None:
            out.append(
                StockQuoteOut(ticker=t, usd=None, status="unavailable", last_updated=None, source=None)
            )
        else:
            out.append(
                StockQuoteOut(
                    ticker=t,
                    usd=hit[0] if hit[3] == "USD" else None,
                    price=hit[0],
                    currency=hit[3],
                    status=status_for(hit[1], now, ttl),
                    last_updated=hit[1],
                    source=hit[2],
                )
            )
    return StockQuotesOut(ttl_seconds=ttl, quotes=out)


# ---------------------------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------------------------
def resolve_symbol(spec: str) -> tuple[str, str, str] | None:
    """(quote symbol, unit, kind) for gold, silver, fx:EUR, crypto:bitcoin, stock:aapl.us, nav:123."""
    kind, _, arg = spec.partition(":")
    kind = kind.lower()
    if spec.lower() == "gold":
        return GOLD, "USD per troy oz", "gold"
    if spec.lower() == "silver":
        return SILVER, "USD per troy oz", "silver"
    if not arg:
        return None
    if kind == "fx":
        return fx_symbol(arg.upper()), "AED per unit", "fx"
    if kind == "crypto":
        return crypto_symbol(arg.lower()), "USD", "crypto"
    if kind == "stock":
        return stock_symbol(arg.lower()), "USD", "stock"
    if kind == "nav":
        return nav_symbol(arg), "NAV (INR)", "nav"
    return None


async def _cached(session: AsyncSession, symbol: str, since: date) -> list[MarketQuote]:
    return list(
        (
            await session.execute(
                select(MarketQuote)
                .where(MarketQuote.symbol == symbol, MarketQuote.quote_date >= since)
                .order_by(MarketQuote.quote_date)
            )
        ).scalars()
    )


async def history(session: AsyncSession, providers: Providers, spec: str, days: int) -> HistoryOut | None:
    resolved = resolve_symbol(spec)
    if resolved is None:
        return None
    symbol, unit, kind = resolved
    since = datetime.now(UTC).date() - timedelta(days=days)
    rows = await _cached(session, symbol, since)
    note = None
    arg = spec.partition(":")[2]
    backfill = {"crypto": providers.crypto, "stock": providers.stocks, "nav": providers.funds}.get(kind)
    if backfill is not None and len(rows) < min(5, days):
        try:
            pts = await backfill.history(arg.lower() if kind != "nav" else arg, days)
            now = datetime.now(UTC)
            for d, v in pts:
                if d >= since:
                    await _upsert(session, symbol, d, v, backfill.name, now)
            await session.commit()
            rows = await _cached(session, symbol, since)
        except ProviderError as exc:
            note = f"History source unavailable: {exc}"
            await session.rollback()
    elif kind in ("gold", "silver", "fx"):
        note = "No free history source: this chart grows from the daily quotes cached since the app started."
    return HistoryOut(
        symbol=spec,
        unit=unit,
        source=rows[-1].source if rows else None,
        points=[HistoryPoint(date=r.quote_date, value=r.value) for r in rows],
        note=note,
    )

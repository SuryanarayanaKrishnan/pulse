"""Cache market quotes, revalue assets from them, and decide which value to show."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset, AssetKind, Liability, ValuationSource
from app.models.market import MarketQuote
from app.models.portfolio import AssetValuation, FundHolding
from app.services.networth import AssetInput
from app.services.valuations import pricing
from app.services.valuations.providers import ProviderError, Providers

log = structlog.get_logger(__name__)

GOLD = "XAU_USD_OZ"
SILVER = "XAG_USD_OZ"
DEFAULT_CRYPTO = ("bitcoin", "ethereum")
NAV_STALE_AFTER_DAYS = 7  # NAVs publish on business days; a week without one means the feed broke


def crypto_symbol(coin_id: str) -> str:
    return f"CRYPTO_{coin_id}_USD"


def stock_symbol(ticker: str) -> str:
    return f"STK_{ticker}_USD"


def nav_symbol(code: str) -> str:
    return f"NAV_{code}"


def fx_symbol(ccy: str) -> str:
    return f"FX_{ccy}_AED"


def index_symbol(key: str) -> str:
    return f"IDX_{key}"


@dataclass(slots=True)
class QuoteBook:
    """Latest cached quotes, read once per operation."""

    gold: tuple[Decimal, datetime, str] | None = None  # usd/oz, fetched_at, source
    silver: tuple[Decimal, datetime, str] | None = None
    fx: dict[str, tuple[Decimal, datetime, str]] = field(default_factory=dict)
    crypto: dict[str, tuple[Decimal, datetime, str]] = field(default_factory=dict)
    stocks: dict[str, tuple[Decimal, datetime, str]] = field(default_factory=dict)
    nav: dict[str, tuple[Decimal, date, datetime, str]] = field(
        default_factory=dict
    )  # nav, nav date, fetched
    indices: dict[str, tuple[list[tuple[date, Decimal]], datetime]] = field(default_factory=dict)

    @property
    def aed_per_usd(self) -> Decimal:
        return self.fx["USD"][0] if "USD" in self.fx else pricing.AED_PER_USD_PEG


async def load_quote_book(session: AsyncSession) -> QuoteBook:
    book = QuoteBook()
    rows = (
        await session.execute(select(MarketQuote).order_by(MarketQuote.symbol, MarketQuote.quote_date))
    ).scalars()
    for q in rows:
        if q.symbol == GOLD:
            book.gold = (q.value, q.fetched_at, q.source)  # ordered by date: the last row wins
        elif q.symbol == SILVER:
            book.silver = (q.value, q.fetched_at, q.source)
        elif q.symbol.startswith("CRYPTO_"):
            book.crypto[q.symbol[7:-4]] = (q.value, q.fetched_at, q.source)
        elif q.symbol.startswith("STK_"):
            book.stocks[q.symbol[4:-4]] = (q.value, q.fetched_at, q.source)
        elif q.symbol.startswith("NAV_"):
            book.nav[q.symbol[4:]] = (q.value, q.quote_date, q.fetched_at, q.source)
        elif q.symbol.startswith("FX_") and q.symbol.endswith("_AED"):
            book.fx[q.symbol[3:-4]] = (q.value, q.fetched_at, q.source)
        elif q.symbol.startswith("IDX_"):
            pts, fetched = book.indices.get(q.symbol[4:], ([], q.fetched_at))
            pts.append((q.quote_date, q.value))
            book.indices[q.symbol[4:]] = (pts, max(fetched, q.fetched_at))
    return book


async def _upsert(
    session: AsyncSession, symbol: str, day: date, value: Decimal, source: str, now: datetime
) -> None:
    stmt = insert(MarketQuote).values(
        symbol=symbol, quote_date=day, value=value, source=source, fetched_at=now
    )
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_market_quotes_symbol_date",
            set_={"value": stmt.excluded.value, "source": stmt.excluded.source, "fetched_at": now},
        )
    )


@dataclass(slots=True)
class RefreshReport:
    gold: str = "skipped"
    fx: str = "skipped"
    property_index: str = "skipped"
    silver: str = "skipped"
    crypto: str = "skipped"
    stocks: str = "skipped"
    funds: str = "skipped"
    assets_revalued: int = 0
    assets_unvalued: int = 0
    errors: list[str] = field(default_factory=list)


async def refresh_quotes(
    session: AsyncSession, providers: Providers, now: datetime | None = None
) -> RefreshReport:
    """Fetch every configured source; one failing never stops the others, and old quotes are kept."""
    now = now or datetime.now(UTC)
    day = now.astimezone(UTC).date()
    rep = RefreshReport()
    if providers.gold:
        try:
            await _upsert(
                session, GOLD, day, await providers.gold.usd_per_troy_oz(), providers.gold.name, now
            )
            rep.gold = "ok"
        except ProviderError as exc:
            rep.gold = "failed"
            rep.errors.append(str(exc))
    if providers.silver:
        try:
            await _upsert(
                session, SILVER, day, await providers.silver.usd_per_troy_oz(), providers.silver.name, now
            )
            rep.silver = "ok"
        except ProviderError as exc:
            rep.silver = "failed"
            rep.errors.append(str(exc))
    want = await wanted_symbols(session)
    if providers.crypto:
        try:
            for cid, price in (await providers.crypto.usd_prices(want.crypto)).items():
                await _upsert(session, crypto_symbol(cid), day, price, providers.crypto.name, now)
            rep.crypto = "ok"
        except ProviderError as exc:
            rep.crypto = "failed"
            rep.errors.append(str(exc))
    if providers.stocks and want.stocks:
        try:
            for tk, price in (await providers.stocks.usd_prices(want.stocks)).items():
                await _upsert(session, stock_symbol(tk), day, price, providers.stocks.name, now)
            rep.stocks = "ok"
        except ProviderError as exc:
            rep.stocks = "failed"
            rep.errors.append(str(exc))
    if providers.funds and want.funds:
        failed = 0
        for code in sorted(want.funds):
            try:
                nav = await providers.funds.latest(code)
                await _upsert(session, nav_symbol(code), nav.nav_date, nav.nav, providers.funds.name, now)
            except ProviderError as exc:
                failed += 1
                rep.errors.append(str(exc))
        rep.funds = "ok" if not failed else ("failed" if failed == len(want.funds) else "partial")
    if providers.fx:
        wanted = {"USD"}
        wanted |= set(
            (
                await session.execute(select(FundHolding.currency).where(FundHolding.deleted_at.is_(None)))
            ).scalars()
        )
        for table, col in (
            (Asset, Asset.currency),
            (Liability, Liability.currency),
            (Account, Account.currency),
        ):
            wanted |= set(
                (await session.execute(select(col).where(table.deleted_at.is_(None)).distinct())).scalars()
            )
        wanted.discard("AED")
        try:
            rates = await providers.fx.aed_per_unit(wanted)
            for ccy, rate in rates.items():
                await _upsert(session, fx_symbol(ccy), day, rate, providers.fx.name, now)
            rep.fx = "ok" if rates else "failed"
            if not rates:
                rep.errors.append("fx source returned no rates")
        except ProviderError as exc:
            rep.fx = "failed"
            rep.errors.append(str(exc))
    if providers.property_index:
        try:
            for key, points in (await providers.property_index.series()).items():
                for d, v in points:
                    await _upsert(session, index_symbol(key), d, v, providers.property_index.name, now)
            rep.property_index = "ok"
        except (ProviderError, ValueError) as exc:
            rep.property_index = "failed"
            rep.errors.append(str(exc))
    await session.commit()
    for err in rep.errors:
        log.warning("valuation_source_failed", error=err)
    return rep


def revalue_asset(asset: Asset, book: QuoteBook) -> bool:
    """Update the asset's automatic value from cached quotes. True when a fresh value was set."""
    if asset.valuation_source is ValuationSource.manual:
        return False
    if asset.currency != "AED":
        asset.valuation_note = "Live valuation only supports AED assets"
        return False
    valued: pricing.Valued | None
    if (
        asset.kind in (AssetKind.gold, AssetKind.gold_jewellery)
        and asset.valuation_source is ValuationSource.market
    ):
        gold = book.gold
        usd_oz, usd_at = (gold[0], gold[1]) if gold else (None, None)
        valued, reason = pricing.value_gold(
            asset.quantity, asset.unit, asset.meta or {}, usd_oz, usd_at, book.aed_per_usd
        )
    elif asset.kind is AssetKind.real_estate and asset.valuation_source is ValuationSource.estimate:
        key = (asset.meta or {}).get("index")
        points, fetched = book.indices.get(str(key), ([], None)) if key else ([], None)
        valued, reason = pricing.value_property(
            asset.meta or {}, asset.cost_basis_minor, asset.acquired_on, points, fetched
        )
    elif (
        asset.kind in (AssetKind.crypto, AssetKind.stock) and asset.valuation_source is ValuationSource.market
    ):
        meta = asset.meta or {}
        if asset.kind is AssetKind.crypto:
            key, quotes, hint = (
                str(meta.get("coingecko_id") or ""),
                book.crypto,
                "metadata.coingecko_id (e.g. bitcoin)",
            )
        else:
            key, quotes, hint = (
                str(meta.get("ticker") or "").lower(),
                book.stocks,
                "metadata.ticker (US, e.g. aapl.us)",
            )
        quote = quotes.get(key) if key else None
        valued, reason = pricing.value_priced(asset.quantity, key, hint, quote, book.aed_per_usd)
    else:
        asset.valuation_note = (
            "Live valuation: gold, jewellery, crypto, stocks (market); real estate (estimate)"
        )
        return False
    if valued is None:
        asset.valuation_note = reason[:240]
        return False
    asset.market_value_minor, asset.market_valued_at, asset.valuation_note = (
        valued.value_minor,
        valued.as_of,
        valued.note[:240],
    )
    return True


async def revalue_assets(
    session: AsyncSession, user_id: UUID | None = None, book: QuoteBook | None = None
) -> tuple[int, int]:
    """(valued, could-not-value) over automatically valued assets; one bad asset never stops the rest."""
    book = book or await load_quote_book(session)
    q = select(Asset).where(Asset.deleted_at.is_(None), Asset.valuation_source != ValuationSource.manual)
    if user_id:
        q = q.where(Asset.user_id == user_id)
    ok = bad = 0
    for asset in (await session.execute(q)).scalars():
        try:
            if revalue_asset(asset, book):
                ok += 1
                await record_valuation(session, asset)
            else:
                bad += 1
        except Exception:
            log.exception("asset_revalue_failed", asset_id=str(asset.id))
            bad += 1
    await session.commit()
    return ok, bad


async def refresh_all(session: AsyncSession, providers: Providers) -> RefreshReport:
    rep = await refresh_quotes(session, providers)
    rep.assets_revalued, rep.assets_unvalued = await revalue_assets(session)
    return rep


def effective(asset: Asset, now: datetime | None = None) -> pricing.EffectiveValue:
    return pricing.effective_value(
        source=asset.valuation_source.value, manual_minor=asset.manual_value_minor, valued_at=asset.valued_at,
        market_minor=asset.market_value_minor, market_at=asset.market_valued_at, note=asset.valuation_note,
        cost_basis_minor=asset.cost_basis_minor, now=now or datetime.now(UTC),
    )  # fmt: skip


def fx_rates_for(base: str, book: QuoteBook, usd_rate: Decimal | None = None) -> dict[str, Decimal]:
    """Currency -> units of ``base``. Only AED-based users get cached rates (they are quoted against AED);
    USD falls back to the fixed AED peg. An explicit ``usd_rate`` always wins."""
    rates: dict[str, Decimal] = {}
    if base == "AED":
        rates = {ccy: v[0] for ccy, v in book.fx.items()}
        rates.setdefault("USD", pricing.AED_PER_USD_PEG)
    if usd_rate and base != "USD":
        rates["USD"] = usd_rate
    return rates


def asset_inputs(assets: list[Asset], now: datetime | None = None) -> list[AssetInput]:
    out = []
    for a in assets:
        eff = effective(a, now)
        out.append(
            AssetInput(
                a.kind.value,
                a.currency,
                eff.value_minor,
                a.cost_basis_minor if eff.value_minor is None else None,
            )
        )
    return out


@dataclass(slots=True)
class Wanted:
    crypto: set[str]
    stocks: set[str]
    funds: set[str]


async def wanted_symbols(session: AsyncSession) -> Wanted:
    crypto, stocks = set(DEFAULT_CRYPTO), set()
    rows = await session.execute(
        select(Asset.kind, Asset.meta).where(
            Asset.deleted_at.is_(None), Asset.kind.in_((AssetKind.crypto, AssetKind.stock))
        )
    )
    for kind, meta in rows:
        if kind is AssetKind.crypto and (meta or {}).get("coingecko_id"):
            crypto.add(str(meta["coingecko_id"]))
        elif kind is AssetKind.stock and (meta or {}).get("ticker"):
            stocks.add(str(meta["ticker"]).lower())
    funds = set(
        (
            await session.execute(
                select(FundHolding.scheme_code).where(
                    FundHolding.deleted_at.is_(None), FundHolding.scheme_code.is_not(None)
                )
            )
        ).scalars()
    )
    return Wanted(crypto, stocks, {f for f in funds if f})


async def record_valuation(session: AsyncSession, asset: Asset) -> None:
    """Keep one valuation point per asset per day (the latest of the day wins)."""
    if asset.market_value_minor is None:
        return
    day = datetime.now(UTC).date()
    stmt = insert(AssetValuation).values(
        user_id=asset.user_id, asset_id=asset.id, valued_on=day, value_minor=asset.market_value_minor,
        basis=asset.valuation_source.value,
    )  # fmt: skip
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_asset_valuations_asset_day",
            set_={"value_minor": stmt.excluded.value_minor, "basis": stmt.excluded.basis},
        )
    )

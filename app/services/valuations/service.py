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
from app.services.networth import AssetInput
from app.services.valuations import pricing
from app.services.valuations.providers import ProviderError, Providers

log = structlog.get_logger(__name__)

GOLD = "XAU_USD_OZ"


def fx_symbol(ccy: str) -> str:
    return f"FX_{ccy}_AED"


def index_symbol(key: str) -> str:
    return f"IDX_{key}"


@dataclass(slots=True)
class QuoteBook:
    """Latest cached quotes, read once per operation."""

    gold: tuple[Decimal, datetime, str] | None = None  # usd/oz, fetched_at, source
    fx: dict[str, tuple[Decimal, datetime, str]] = field(default_factory=dict)
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
    if providers.fx:
        wanted = {"USD"}
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
    if asset.kind is AssetKind.gold and asset.valuation_source is ValuationSource.market:
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
    else:
        asset.valuation_note = "Live valuation is available for gold (market) and real estate (estimate)"
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

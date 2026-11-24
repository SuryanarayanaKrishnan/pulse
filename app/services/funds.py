"""Mutual-fund valuation (units x latest NAV, converted to AED) and CAS import."""

import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import FundHolding, FundKind, FundTransaction
from app.schemas.funds import CasImportOut, FundsOut, HoldingOut, NavInfo
from app.services.cas import CasHolding, CasResult
from app.services.networth import AssetInput
from app.services.valuations import pricing
from app.services.valuations.providers import FundProvider, ProviderError
from app.services.valuations.service import NAV_STALE_AFTER_DAYS, QuoteBook


def aed_rate(currency: str, book: QuoteBook) -> Decimal | None:
    if currency == "AED":
        return Decimal(1)
    if currency in book.fx:
        return book.fx[currency][0]
    return pricing.AED_PER_USD_PEG if currency == "USD" else None


def _minor(amount: Decimal) -> int:
    """Major units to minor units."""
    return pricing.to_minor(amount)


def _scale(minor: int, rate: Decimal) -> int:
    """A minor-unit amount converted at ``rate`` (stays in minor units)."""
    return int((Decimal(minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def holding_out(h: FundHolding, book: QuoteBook, now: datetime | None = None) -> HoldingOut:
    now = now or datetime.now(UTC)
    nav: Decimal | None = None
    as_of: date | None = None
    updated: datetime | None = None
    basis = "none"
    note: str | None = None
    quote = book.nav.get(h.scheme_code) if h.scheme_code else None
    if quote is not None:
        q_nav, q_date, fetched, _src = quote
        if (now.date() - q_date).days <= NAV_STALE_AFTER_DAYS:
            nav, as_of, updated, basis = q_nav, q_date, fetched, "market"
        else:
            note = f"Latest published NAV is from {q_date.isoformat()}"
            nav, as_of, updated, basis = q_nav, q_date, fetched, "stale_market"
    if basis != "market" and h.manual_nav is not None:
        nav = h.manual_nav
        as_of = h.manual_nav_at.date() if h.manual_nav_at else None
        updated, basis = h.manual_nav_at, "manual"
        note = "Using your entered NAV" + (f" ({note})" if note else "")
    elif basis == "none":
        note = "No NAV yet: add a scheme code for live NAVs or enter one manually"
    value = _minor(h.units * nav) if nav is not None else None
    rate = aed_rate(h.currency, book)
    value_aed = _scale(value, rate) if value is not None and rate is not None else None
    if value is not None and rate is None:
        note = (note + "; " if note else "") + f"No {h.currency}/AED rate cached yet"
    cost = _minor(h.units * h.avg_cost_per_unit) if h.avg_cost_per_unit is not None else None
    gain = value - cost if value is not None and cost is not None else None
    pct = round(gain / cost * 100, 2) if gain is not None and cost else None
    return HoldingOut(
        id=h.id,
        created_at=h.created_at,
        updated_at=h.updated_at,
        kind=h.kind,
        name=h.name,
        scheme_code=h.scheme_code,
        isin=h.isin,
        folio=h.folio,
        currency=h.currency,
        units=h.units,
        avg_cost_per_unit=h.avg_cost_per_unit,
        sip_amount_minor=h.sip_amount_minor,
        sip_day=h.sip_day,
        nav=NavInfo(value=nav, as_of=as_of, basis=basis),
        value_minor=value,
        value_aed_minor=value_aed,
        fx_rate_to_aed=rate,
        cost_minor=cost,
        gain_minor=gain,
        gain_pct=pct,
        last_updated=updated,
        note=note,
    )


async def user_holdings(session: AsyncSession, user_id: UUID) -> list[FundHolding]:
    rows = await session.execute(
        select(FundHolding)
        .where(FundHolding.user_id == user_id, FundHolding.deleted_at.is_(None))
        .order_by(FundHolding.created_at)
    )
    return list(rows.scalars())


def summarise(holdings: list[HoldingOut]) -> FundsOut:
    total = sum(h.value_aed_minor or 0 for h in holdings)
    costs = [
        _scale(h.cost_minor, h.fx_rate_to_aed)
        for h in holdings
        if h.cost_minor is not None and h.fx_rate_to_aed is not None
    ]
    unconverted = {h.currency for h in holdings if h.value_minor is not None and h.value_aed_minor is None}
    return FundsOut(
        holdings=holdings,
        total_value_aed_minor=total,
        total_cost_aed_minor=sum(costs) if costs else None,
        unconverted=sorted(unconverted),
    )


def asset_inputs(holdings: list[HoldingOut]) -> list[AssetInput]:
    """Funds as net-worth assets in their own currency (converted later with the cached FX rates)."""
    return [AssetInput("fund", h.currency, h.value_minor) for h in holdings if h.value_minor is not None]


# ---------------------------------------------------------------------------------------------
# CAS import
# ---------------------------------------------------------------------------------------------
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


async def resolve_scheme(provider: FundProvider | None, name: str) -> str | None:
    """AMFI code by name: only an unambiguous match (exact normalised name, or the single search hit)."""
    if provider is None:
        return None
    try:
        hits = await provider.search(" ".join(name.split()[:6]))
    except ProviderError:
        return None
    exact = [h for h in hits if _norm(h.name) == _norm(name)]
    if len(exact) == 1:
        return exact[0].code
    return hits[0].code if len(hits) == 1 else None


async def import_cas(
    session: AsyncSession, user_id: UUID, result: CasResult, provider: FundProvider | None, book: QuoteBook
) -> CasImportOut:
    created = updated = added = dup = 0
    unresolved: list[str] = []
    touched: list[FundHolding] = []
    existing = await user_holdings(session, user_id)
    for ch in result.holdings:
        h = _match(existing, ch)
        if h is None:
            h = FundHolding(
                user_id=user_id, kind=FundKind.india_mf, name=ch.name, currency="INR", units=Decimal(0)
            )
            session.add(h)
            existing.append(h)
            created += 1
        else:
            updated += 1
        h.isin, h.folio, h.name = ch.isin or h.isin, ch.folio, ch.name
        h.units = ch.units
        if ch.avg_cost_per_unit is not None:
            h.avg_cost_per_unit = ch.avg_cost_per_unit
        if ch.nav is not None and ch.nav_date is not None:  # fallback NAV until a live one arrives
            h.manual_nav = ch.nav
            h.manual_nav_at = datetime(ch.nav_date.year, ch.nav_date.month, ch.nav_date.day, tzinfo=UTC)
        if not h.scheme_code:
            h.scheme_code = await resolve_scheme(provider, ch.name)
        if not h.scheme_code:
            unresolved.append(ch.name)
        await session.flush()
        for t in ch.transactions:
            stmt = (
                insert(FundTransaction)
                .values(
                    user_id=user_id,
                    holding_id=h.id,
                    txn_date=t.txn_date,
                    kind=t.kind,
                    units=t.units,
                    nav=t.nav,
                    amount_minor=t.amount_minor,
                    description=t.description[:300],
                    source="cas",
                    dedupe_hash=t.dedupe_hash,
                )
                .on_conflict_do_nothing(constraint="uq_fund_transactions_holding_hash")
                .returning(FundTransaction.id)
            )
            if (await session.execute(stmt)).first():
                added += 1
            else:
                dup += 1
        touched.append(h)
    await session.commit()
    for h in touched:
        await session.refresh(h)
    return CasImportOut(
        holdings_created=created,
        holdings_updated=updated,
        transactions_added=added,
        transactions_skipped_duplicate=dup,
        unresolved_schemes=unresolved,
        warnings=result.warnings,
        holdings=[holding_out(h, book) for h in touched],
    )


def _match(existing: list[FundHolding], ch: CasHolding) -> FundHolding | None:
    for h in existing:
        if h.folio == ch.folio and ((ch.isin and h.isin == ch.isin) or _norm(h.name) == _norm(ch.name)):
            return h
    return None

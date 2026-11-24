"""Rewards on top of captured transactions: cycle spend per card, best card for a purchase, nudges."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.card import CatalogCard, UserCard
from app.models.category import Category
from app.models.transaction import Direction, Transaction
from app.schemas.rewards import CapOut, CardRewardsOut, CatalogCardOut, FeeWaiverOut
from app.services import rewards as engine
from app.services.merchants import guess_category_slug, merchant_key, normalize_merchant
from app.services.parsers.dates import DUBAI, to_dubai_date

NEAR_CAP_PCT = Decimal(80)


def catalog_out(c: CatalogCard) -> CatalogCardOut:
    return CatalogCardOut(
        id=c.id,
        issuer=c.issuer,
        name=c.name,
        network=c.network,
        annual_fee_minor=c.annual_fee_minor,
        min_salary_minor=c.min_salary_minor,
        reward_structure=c.reward_structure,
        perks=c.perks,
        verified_on=c.verified_at.date() if c.verified_at else None,
        source=c.source_url,
        data_status="verified" if c.verified_at else "unverified",
    )


class CardCtx:
    def __init__(self, user_card: UserCard, catalog: CatalogCard, account: Account | None) -> None:
        self.user_card, self.catalog, self.account = user_card, catalog, account
        self.spec = engine.parse_structure(catalog.reward_structure)

    @property
    def title(self) -> str:
        return self.user_card.nickname or self.catalog.name


async def load_cards(session: AsyncSession, user_id: UUID) -> list[CardCtx]:
    rows = await session.execute(
        select(UserCard, CatalogCard, Account)
        .join(CatalogCard, CatalogCard.id == UserCard.catalog_card_id)
        .outerjoin(Account, Account.id == UserCard.account_id)
        .where(UserCard.user_id == user_id, UserCard.deleted_at.is_(None))
        .order_by(UserCard.created_at)
    )
    out: list[CardCtx] = []
    for uc, cat, acc in rows:
        try:
            out.append(CardCtx(uc, cat, acc))
        except (ValueError, KeyError):
            continue  # a malformed catalog entry never breaks the user's screen
    return out


async def category_slugs(session: AsyncSession, user_id: UUID) -> dict[UUID, str | None]:
    """category id -> default slug (a custom sub-category inherits its parent's slug)."""
    rows = list(
        (
            await session.execute(
                select(Category).where((Category.user_id.is_(None)) | (Category.user_id == user_id))
            )
        ).scalars()
    )
    by_id = {c.id: c for c in rows}
    out: dict[UUID, str | None] = {}
    for c in rows:
        cur: Category | None = c
        while cur is not None and cur.slug is None and cur.parent_id is not None:
            cur = by_id.get(cur.parent_id)
        out[c.id] = cur.slug if cur else None
    return out


def _bounds_dt(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime(start.year, start.month, start.day, tzinfo=DUBAI),
        datetime(end.year, end.month, end.day, tzinfo=DUBAI) + timedelta(days=1),
    )


async def cycle_spends(
    session: AsyncSession, user_id: UUID, ctx: CardCtx, start: date, end: date, slugs: dict[UUID, str | None]
) -> list[engine.Spend]:
    if ctx.account is None:
        return []
    lo, hi = _bounds_dt(start, end)
    rows = await session.execute(
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.account_id == ctx.account.id,
            Transaction.deleted_at.is_(None),
            Transaction.direction == Direction.debit,
            Transaction.currency == "AED",
            Transaction.posted_at >= lo,
            Transaction.posted_at < hi,
        )
        .order_by(Transaction.posted_at, Transaction.id)
    )
    return [
        engine.Spend(
            t.amount_minor,
            slugs.get(t.category_id) if t.category_id else None,
            (merchant_key(t.merchant_normalized) if t.merchant_normalized else None),
        )
        for t in rows.scalars()
    ]


async def year_spend(session: AsyncSession, user_id: UUID, ctx: CardCtx, today: date) -> int:
    if ctx.account is None:
        return 0
    lo, hi = _bounds_dt(date(today.year, 1, 1), today)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.user_id == user_id,
                Transaction.account_id == ctx.account.id,
                Transaction.deleted_at.is_(None),
                Transaction.direction == Direction.debit,
                Transaction.currency == "AED",
                Transaction.posted_at >= lo,
                Transaction.posted_at < hi,
            )
        )
    ).scalar_one()
    return int(total)


async def card_state(
    session: AsyncSession, user_id: UUID, ctx: CardCtx, today: date, slugs: dict[UUID, str | None]
) -> tuple[engine.CycleResult, date, date]:
    start, end = engine.cycle_bounds(today, ctx.account.statement_day if ctx.account else None)
    return (
        engine.compute_cycle(ctx.spec, await cycle_spends(session, user_id, ctx, start, end, slugs)),
        start,
        end,
    )


async def summarise_card(
    session: AsyncSession, user_id: UUID, ctx: CardCtx, today: date, slugs: dict[UUID, str | None]
) -> CardRewardsOut:
    state, start, end = await card_state(session, user_id, ctx, today, slugs)
    spec = ctx.spec
    raw_value = spec.value_minor(state.earned_units)
    waiver = None
    if spec.fee_waiver_spend_minor:
        spent = await year_spend(session, user_id, ctx, today)
        waiver = FeeWaiverOut(
            annual_fee_minor=ctx.catalog.annual_fee_minor,
            threshold_minor=spec.fee_waiver_spend_minor,
            spend_this_year_minor=spent,
            progress_pct=min(Decimal(100), Decimal(spent) / spec.fee_waiver_spend_minor * 100).quantize(
                Decimal("0.1")
            ),
            waived=spent >= spec.fee_waiver_spend_minor,
        )
    caps = [
        CapOut(
            rule=u.rule,
            cap_units=u.cap_units,
            used_units=u.used_units.quantize(Decimal("0.01")),
            remaining_units=u.remaining_units.quantize(Decimal("0.01"))
            if u.remaining_units is not None
            else None,
            pct_used=u.pct_used.quantize(Decimal("0.1")) if u.pct_used is not None else None,
        )
        for u in state.usage.values()
        if u.cap_units is not None
    ]
    cat = catalog_out(ctx.catalog)
    return CardRewardsOut(
        user_card_id=ctx.user_card.id,
        name=ctx.catalog.name,
        issuer=ctx.catalog.issuer,
        nickname=ctx.user_card.nickname,
        account_id=ctx.user_card.account_id,
        unit=spec.unit,
        cycle_start=start,
        cycle_end=end,
        days_left=max(0, (end - today).days),
        spend_minor=state.spend_minor,
        earned_units=state.earned_units.quantize(Decimal("0.01")),
        earned_value_minor=state.earned_value_minor(spec),
        pending_value_minor=0 if state.min_spend_met else raw_value,
        min_spend_required_minor=spec.min_monthly_spend_minor,
        min_spend_met=state.min_spend_met,
        caps=caps,
        fee_waiver=waiver,
        data_status=cat.data_status,
        verified_on=cat.verified_on,
        source=cat.source,
    )


def resolve_target(category: str | None, merchant: str | None) -> tuple[str | None, str | None]:
    """(category slug, merchant key) for a best-card question; a known merchant implies its category."""
    key = merchant_key(normalize_merchant(merchant)) if merchant else None
    if category is None and merchant:
        category = guess_category_slug(merchant, Direction.debit)
    return category, key


async def rank_cards(
    session: AsyncSession,
    user_id: UUID,
    cards: list[CardCtx],
    category: str | None,
    merchant_key_: str | None,
    amount_minor: int,
    today: date,
    slugs: dict[UUID, str | None],
    exclude: UUID | None = None,
) -> list[tuple[CardCtx, engine.Quote]]:
    ranked: list[tuple[CardCtx, engine.Quote]] = []
    for ctx in cards:
        if ctx.user_card.id == exclude:
            continue
        state, _, _ = await card_state(session, user_id, ctx, today, slugs)
        ranked.append(
            (ctx, engine.quote(ctx.spec, state, engine.Spend(amount_minor, category, merchant_key_)))
        )
    ranked.sort(key=lambda cq: (-cq[1].value_minor, cq[0].user_card.created_at))
    return ranked


async def nudge_for_transaction(
    session: AsyncSession, user_id: UUID, txn: Transaction
) -> dict[str, Any] | None:
    """After a capture: if the card's cap for this spend's category is reached (or nearly), point at the
    user's other card that earns more for it. None when nothing useful can be said."""
    if txn.direction is not Direction.debit:
        return None
    cards = await load_cards(session, user_id)
    ctx = next((c for c in cards if c.user_card.account_id == txn.account_id), None)
    if ctx is None:
        return None
    slugs = await category_slugs(session, user_id)
    category = slugs.get(txn.category_id) if txn.category_id else None
    mkey = merchant_key(txn.merchant_normalized) if txn.merchant_normalized else None
    rule = engine.select_rule(ctx.spec, category, mkey)
    if rule is None or rule.cap_units is None:
        return None
    today = to_dubai_date(datetime.now(UTC))
    state, _, end = await card_state(session, user_id, ctx, today, slugs)
    used = state.usage.get(rule.name)
    pct = used.pct_used if used else None
    if pct is None or pct < NEAR_CAP_PCT:
        return None
    level = "capped" if pct >= 100 else "near_cap"
    unit = "AED" if ctx.spec.unit == "cashback" else "points"
    base = f"{ctx.title}: {rule.name} {'cap reached' if level == 'capped' else f'{pct:.0f}% of the cap used'}"
    alternatives = await rank_cards(
        session, user_id, cards, category, mkey, 10_000, today, slugs, exclude=ctx.user_card.id
    )
    mine = engine.quote(ctx.spec, state, engine.Spend(10_000, category, mkey)).value_minor
    best = alternatives[0] if alternatives and alternatives[0][1].value_minor > mine else None
    if best is None:
        message = f"{base} (until {end.isoformat()}); no other card of yours earns more for this."
        return {
            "level": level,
            "message": message,
            "card_id": str(ctx.user_card.id),
            "suggested_card_id": None,
            "extra_value_minor": 0,
            "cap_unit": unit,
        }
    best_ctx, best_q = best
    message = (
        f"{base}. Use {best_ctx.title} for {rule.name.lower()} until {end.isoformat()}: "
        f"it earns about AED {best_q.value_minor / 100:.2f} per AED 100 vs AED {mine / 100:.2f}."
    )
    return {
        "level": level,
        "message": message,
        "card_id": str(ctx.user_card.id),
        "suggested_card_id": str(best_ctx.user_card.id),
        "extra_value_minor": best_q.value_minor - mine,
        "cap_unit": unit,
    }

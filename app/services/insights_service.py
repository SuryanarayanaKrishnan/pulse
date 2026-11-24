"""Database loaders that feed the pure insight modules (cashflow, dues, safe-to-spend, health)."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.asset import Asset, Liability
from app.models.insights import NetWorthSnapshot, SavingsGoal
from app.models.statement import ParseStatus, Statement
from app.models.transaction import Direction, Transaction
from app.services import cashflow as cf
from app.services import dues, health
from app.services import goals as goal_math
from app.services import safe_to_spend as sts
from app.services.balances import Balance, account_balances
from app.services.merchants import merchant_key
from app.services.networth import LiabilityInput, compute_net_worth
from app.services.parsers.dates import DUBAI, to_dubai_date
from app.services.recurring import add_months
from app.services.recurring_service import detect_for_user, upcoming_items
from app.services.rewards_service import category_slugs
from app.services.salary import Payment, summarise, user_records
from app.services.valuations.service import asset_inputs, fx_rates_for, load_quote_book

LIQUID_TYPES = (AccountType.bank, AccountType.cash, AccountType.wallet)


def today_dubai() -> date:
    return to_dubai_date(datetime.now(UTC))


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=DUBAI)


@dataclass(frozen=True, slots=True)
class TxnRow:
    day: date
    amount_minor: int
    is_credit: bool
    slug: str | None
    category_id: UUID | None
    excluded: bool
    merchant: str | None
    key: str | None
    account_id: UUID

    def flow(self) -> cf.Flow:
        return cf.Flow(self.day, self.amount_minor, self.is_credit, self.slug, self.excluded)


async def load_txns(session: AsyncSession, user_id: UUID, since: date) -> list[TxnRow]:
    slugs = await category_slugs(session, user_id)
    card_accounts = set(
        (
            await session.execute(
                select(Account.id).where(Account.user_id == user_id, Account.type == AccountType.credit_card)
            )
        ).scalars()
    )
    rows = await session.execute(
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.currency == "AED",
            Transaction.posted_at >= day_start(since),
        )
        .order_by(Transaction.posted_at)
    )
    out: list[TxnRow] = []
    for t in rows.scalars():
        name = t.merchant_normalized or t.merchant_raw
        out.append(
            TxnRow(
                to_dubai_date(t.posted_at), t.amount_minor, t.direction is Direction.credit,
                slugs.get(t.category_id) if t.category_id else None, t.category_id,
                # a credit on a card is a payment or refund, never income
                t.exclude_from_budget or (t.direction is Direction.credit and t.account_id in card_accounts),
                name.strip() if name else None, merchant_key(name), t.account_id,
            )
        )  # fmt: skip
    return out


@dataclass(slots=True)
class Liquid:
    total_minor: int | None
    unknown_accounts: int


def liquid_total(balances: dict[UUID, Balance]) -> Liquid:
    known = [
        b for b in balances.values() if b.account_type in LIQUID_TYPES and b.currency == "AED" and b.known
    ]
    unknown = sum(
        1 for b in balances.values() if b.account_type in LIQUID_TYPES and b.currency == "AED" and not b.known
    )
    total = sum(b.balance_minor or 0 for b in known) if known else None
    return Liquid(total, unknown)


# ---------------------------------------------------------------------------------------------
# Card statements and dues
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CardStatement:
    account_id: UUID
    statement_date: date | None
    due_date: date | None
    min_due_minor: int | None
    total_due_minor: int | None  # closing balance = amount owed at the statement date
    period_end: date | None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


async def card_statements(session: AsyncSession, user_id: UUID) -> list[CardStatement]:
    rows = await session.execute(
        select(Statement)
        .where(
            Statement.user_id == user_id,
            Statement.deleted_at.is_(None),
            Statement.parse_status == ParseStatus.parsed,
            Statement.kind == "credit_card",
            Statement.account_id.is_not(None),
        )
        .order_by(Statement.period_end.desc().nulls_last(), Statement.created_at.desc())
    )
    out: list[CardStatement] = []
    for st in rows.scalars():
        assert st.account_id is not None
        summary = st.summary or {}
        out.append(
            CardStatement(
                st.account_id,
                _date(summary.get("statement_date")) or st.period_end,
                _date(summary.get("due_date")),
                summary.get("min_due_minor"),
                abs(st.closing_balance_minor) if st.closing_balance_minor is not None else None,
                st.period_end,
            )
        )
    return out


async def _credits_between(
    session: AsyncSession, user_id: UUID, account_id: UUID, start: datetime, end: datetime | None
) -> int:
    q = select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
        Transaction.user_id == user_id,
        Transaction.account_id == account_id,
        Transaction.deleted_at.is_(None),
        Transaction.direction == Direction.credit,
        Transaction.posted_at >= start,
    )
    if end is not None:
        q = q.where(Transaction.posted_at < end)
    return int((await session.execute(q)).scalar_one())


async def card_dues(
    session: AsyncSession, user_id: UUID, today: date, balances: dict[UUID, Balance] | None = None
) -> list[dues.CardDue]:
    balances = balances if balances is not None else await account_balances(session, user_id)
    accounts = (
        await session.execute(
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.deleted_at.is_(None),
                Account.type == AccountType.credit_card,
            )
            .order_by(Account.created_at)
        )
    ).scalars()
    latest: dict[UUID, CardStatement] = {}
    for s in await card_statements(session, user_id):
        latest.setdefault(s.account_id, s)
    out: list[dues.CardDue] = []
    for acc in accounts:
        st = latest.get(acc.id)
        bal = balances.get(acc.id)
        paid = 0
        if st and st.statement_date:
            paid = await _credits_between(
                session, user_id, acc.id, day_start(st.statement_date) + timedelta(days=1), None
            )
        out.append(
            dues.card_due(
                dues.CardInput(
                    account_id=str(acc.id), name=acc.name, last4=acc.last4,
                    credit_limit_minor=acc.credit_limit_minor, due_day=acc.due_day,
                    statement_date=st.statement_date if st else None,
                    due_date=st.due_date if st else None,
                    min_due_minor=st.min_due_minor if st else None,
                    total_due_minor=st.total_due_minor if st else None,
                    paid_since_statement_minor=paid,
                    owed_now_minor=bal.balance_minor if bal and bal.known else None,
                ),
                today,
            )
        )  # fmt: skip
    out.sort(key=lambda d: (d.due_date is None, d.due_date or date.max))
    return out


# ---------------------------------------------------------------------------------------------
# Salary, horizon
# ---------------------------------------------------------------------------------------------
async def salary_summary(session: AsyncSession, user_id: UUID, today: date) -> Any:
    records = await user_records(session, user_id)
    return summarise([Payment(r.received_on, r.amount_minor, r.employer, r.currency) for r in records], today)


def month_end(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def horizon(today: date, salary: Any) -> tuple[date, str]:
    nxt = salary.next_expected_on
    if salary.is_recurring and nxt is not None and nxt > today and salary.currency in (None, "AED"):
        return nxt, "salary"
    end = month_end(today)
    if end <= today:
        end = month_end(today + timedelta(days=1))
    return end, "month_end"


# ---------------------------------------------------------------------------------------------
# Cashflow + projection
# ---------------------------------------------------------------------------------------------
async def cashflow_report(
    session: AsyncSession, user_id: UUID, today: date, months: int, projection_days: int
) -> tuple[list[cf.MonthFlow], dict[str, Any]]:
    first = cf.months_back(today, max(months, 4))[0]
    txns = await load_txns(session, user_id, first)
    monthly = cf.monthly_cashflow([t.flow() for t in txns], today, months)
    proj = await projection(session, user_id, today, projection_days, txns)
    return monthly, proj


async def projection(
    session: AsyncSession, user_id: UUID, today: date, days: int, txns: list[TxnRow] | None = None
) -> dict[str, Any]:
    balances = await account_balances(session, user_id)
    liquid = liquid_total(balances)
    if liquid.total_minor is None:
        return {
            "available": False,
            "reason": "No account balance is known: set one on a bank, cash or wallet account.",
        }
    end = today + timedelta(days=days)
    det = await detect_for_user(session, user_id, today)
    events: list[cf.Event] = []
    for item in await upcoming_items(session, user_id, today, end, det):
        events.append(
            cf.Event(item.date, item.name, -item.amount_minor, "loan" if item.kind == "loan" else "bill")
        )
    for d in await card_dues(session, user_id, today, balances):
        if d.remaining_total_due_minor and d.due_date and d.due_date <= end and d.status != "paid":
            events.append(
                cf.Event(
                    max(d.due_date, today), f"{d.name} card payment", -d.remaining_total_due_minor, "card_due"
                )
            )
    sal = await salary_summary(session, user_id, today)
    if (
        sal.is_recurring
        and sal.average_monthly_minor
        and sal.next_expected_on
        and sal.currency in (None, "AED")
    ):
        sd = sal.next_expected_on
        while sd < today:
            sd = add_months(sd, 1, sal.typical_day)
        while sd <= end:
            events.append(cf.Event(sd, "Salary (expected)", sal.average_monthly_minor, "salary"))
            sd = add_months(sd, 1, sal.typical_day)
    rows = txns if txns is not None else await load_txns(session, user_id, today - timedelta(days=90))
    recurring_keys = {s.key for s in det.series if s.status != "lapsed"}
    since = today - timedelta(days=90)
    variable = sum(
        t.amount_minor for t in rows
        if t.day >= since and cf.is_spend(t.flow()) and (t.key is None or t.key not in recurring_keys)
    )  # fmt: skip
    daily = round(variable / 90)
    path = cf.project_balance(liquid.total_minor, today, days, events, daily)
    lowest = min(path, key=lambda p: p.balance_minor)
    negative = next((p.day for p in path if p.balance_minor < 0), None)
    notes = [
        "Starts from the known balances of your bank, cash and wallet accounts.",
        "Each day spends your 90-day average of non-recurring spending (card spend counts when spent).",
        "Recurring bills, loan EMIs, card statement balances and expected salary land on their due dates.",
    ]
    if liquid.unknown_accounts:
        notes.append(f"{liquid.unknown_accounts} account(s) without a known balance are left out.")
    return {
        "available": True, "start_balance_minor": liquid.total_minor, "daily_variable_spend_minor": daily,
        "lowest_balance_minor": lowest.balance_minor, "lowest_on": lowest.day, "goes_negative_on": negative,
        "days": path, "assumptions": notes,
    }  # fmt: skip


# ---------------------------------------------------------------------------------------------
# Safe to spend
# ---------------------------------------------------------------------------------------------
async def safe_to_spend(
    session: AsyncSession, user_id: UUID, today: date
) -> tuple[sts.SafeToSpend, date, str, list[str]]:
    balances = await account_balances(session, user_id)
    liquid = liquid_total(balances)
    sal = await salary_summary(session, user_id, today)
    end, basis = horizon(today, sal)
    commitments: list[sts.Commitment] = []
    notes: list[str] = []
    for item in await upcoming_items(session, user_id, today, end):
        commitments.append(
            sts.Commitment("loan" if item.kind == "loan" else "bill", item.name, item.amount_minor, item.date)
        )
    skipped_cards = 0
    for d in await card_dues(session, user_id, today, balances):
        if d.status == "paid" or not d.remaining_total_due_minor:
            continue
        if d.due_date and (d.due_date <= end or d.status == "overdue"):
            commitments.append(sts.Commitment("card_due", d.name, d.remaining_total_due_minor, d.due_date))
        else:
            skipped_cards += 1
    if skipped_cards:
        notes.append(
            f"{skipped_cards} card statement(s) fall due after {end.isoformat()} and are not deducted yet."
        )
    days_left = max(1, (end - today).days)
    for g in (
        await session.execute(
            select(SavingsGoal).where(SavingsGoal.user_id == user_id, SavingsGoal.deleted_at.is_(None))
        )
    ).scalars():
        reserve = goal_math.monthly_reserve(
            max(0, g.target_minor - g.saved_minor), g.monthly_contribution_minor, today, g.target_date
        )
        part = -(-reserve * min(days_left, 30) // 30)
        if part > 0:
            commitments.append(sts.Commitment("goal", g.name, part, end))
    if liquid.total_minor is None:
        notes.append(
            "No account balance is known, so there is nothing to subtract from: set one on an account."
        )
    elif liquid.unknown_accounts:
        notes.append(f"{liquid.unknown_accounts} account(s) without a known balance are not counted.")
    if any(c.kind == "bill" and c.amount_minor for c in commitments):
        notes.append("Variable bills (utilities, phone) use your recent typical amount.")
    return sts.compute(liquid.total_minor, commitments, today, end), end, basis, notes


# ---------------------------------------------------------------------------------------------
# Net-worth snapshots
# ---------------------------------------------------------------------------------------------
async def snapshot_values(session: AsyncSession, user_id: UUID) -> tuple[int, int, dict[str, Any]]:
    assets = (
        (await session.execute(select(Asset).where(Asset.user_id == user_id, Asset.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    liabilities = (
        (
            await session.execute(
                select(Liability).where(Liability.user_id == user_id, Liability.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    book = await load_quote_book(session)
    nw = compute_net_worth(
        asset_inputs(list(assets)),
        [LiabilityInput(x.kind.value, x.currency, x.outstanding_minor) for x in liabilities],
        fx_rates=fx_rates_for("AED", book),
    )
    balances = await account_balances(session, user_id)
    accounts_minor = liquid_total(balances).total_minor or 0
    linked = {li.account_id for li in liabilities if li.account_id}
    cards_minor = sum(
        max(0, b.balance_minor or 0)
        for b in balances.values()
        if b.account_type is AccountType.credit_card
        and b.known
        and b.currency == "AED"
        and b.account_id not in linked
    )
    return (
        nw.total_assets_minor + accounts_minor,
        nw.total_liabilities_minor + cards_minor,
        {
            "assets_by_kind": nw.assets_by_kind, "liabilities_by_kind": nw.liabilities_by_kind,
            "accounts_minor": accounts_minor, "card_balances_minor": cards_minor,
            "unconverted_currencies": nw.unconverted_currencies,
        },
    )  # fmt: skip


async def take_snapshot(session: AsyncSession, user_id: UUID, day: date) -> NetWorthSnapshot:
    assets, liabilities, breakdown = await snapshot_values(session, user_id)
    stmt = (
        insert(NetWorthSnapshot)
        .values(
            user_id=user_id, snapshot_date=day, currency="AED", assets_minor=assets,
            liabilities_minor=liabilities, net_worth_minor=assets - liabilities, breakdown=breakdown,
        )
        .on_conflict_do_update(
            constraint="uq_net_worth_snapshots_user_date",
            set_={
                "assets_minor": assets, "liabilities_minor": liabilities,
                "net_worth_minor": assets - liabilities, "breakdown": breakdown, "updated_at": func.now(),
            },
        )
        .returning(NetWorthSnapshot)
    )  # fmt: skip
    row = (await session.execute(stmt.execution_options(populate_existing=True))).scalar_one()
    await session.commit()
    return row


# ---------------------------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------------------------
async def health_score(session: AsyncSession, user_id: UUID, today: date) -> health.Health:
    months = cf.months_back(today, 4)[:3]  # the three complete months before this one
    txns = await load_txns(session, user_id, months[0])
    flows = cf.monthly_cashflow([t.flow() for t in txns], months[-1].replace(day=28), 3)
    income = sum(m.income_minor for m in flows)
    spend = sum(m.spend_minor for m in flows)
    rate = (income - spend) / income if income > 0 else None
    balances = await account_balances(session, user_id)
    liquid = liquid_total(balances)
    cards = [
        b for b in balances.values()
        if b.account_type is AccountType.credit_card and b.known and b.currency == "AED"
    ]  # fmt: skip
    limits = {
        a.id: a.credit_limit_minor
        for a in (
            await session.execute(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.deleted_at.is_(None),
                    Account.type == AccountType.credit_card,
                )
            )
        ).scalars()
        if a.credit_limit_minor
    }
    with_limit = [b for b in cards if b.account_id in limits]
    owed = sum(max(0, b.balance_minor or 0) for b in with_limit) if with_limit else None
    limit_total = sum(limits[b.account_id] for b in with_limit) if with_limit else None
    on_time = total = 0
    for st in (await card_statements(session, user_id))[:12]:
        if not (st.due_date and st.statement_date and st.due_date < today):
            continue
        need = st.min_due_minor if st.min_due_minor is not None else st.total_due_minor
        if need is None:
            continue
        paid = await _credits_between(
            session, user_id, st.account_id,
            day_start(st.statement_date) + timedelta(days=1), day_start(st.due_date) + timedelta(days=1),
        )  # fmt: skip
        total += 1
        on_time += 1 if paid >= need else 0
    avg_spend = spend // 3 if spend else None
    return health.compute(
        [
            health.savings_component(rate),
            health.utilisation_component(owed, limit_total),
            health.on_time_component(on_time, total),
            health.runway_component(liquid.total_minor, avg_spend),
        ]
    )


# ---------------------------------------------------------------------------------------------
# Spend trends
# ---------------------------------------------------------------------------------------------
async def spend_trends(
    session: AsyncSession, user_id: UUID, today: date, n_months: int, top: int
) -> dict[str, Any]:
    from collections import defaultdict

    from app.models.category import Category

    month_list = cf.months_back(today, n_months)
    txns = [t for t in await load_txns(session, user_id, month_list[0]) if cf.is_spend(t.flow())]
    cats = {
        c.id: c
        for c in (
            await session.execute(
                select(Category).where((Category.user_id.is_(None)) | (Category.user_id == user_id))
            )
        ).scalars()
    }

    def top_level(cid: UUID | None) -> Category | None:
        c = cats.get(cid) if cid else None
        while c is not None and c.parent_id is not None and c.parent_id in cats:
            c = cats[c.parent_id]
        return c

    per_cat: dict[UUID | None, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[date, int] = defaultdict(int)
    merchants: dict[str, list[Any]] = {}
    for t in txns:
        m = cf.month_start(t.day)
        root = top_level(t.category_id)
        per_cat[root.id if root else None][m] += t.amount_minor
        totals[m] += t.amount_minor
        if t.key and t.merchant:
            row = merchants.setdefault(t.key, [t.merchant, 0, 0, t.day])
            row[1] += t.amount_minor
            row[2] += 1
            row[3] = max(row[3], t.day)
    categories: list[dict[str, Any]] = []
    for cid, by_month in per_cat.items():
        cat = cats.get(cid) if cid else None
        series = [{"month": m, "total_minor": by_month.get(m, 0)} for m in month_list]
        # last *complete* month against the one before it (the current month is still filling up)
        latest = by_month.get(month_list[-2], 0) if len(month_list) > 2 else 0
        prev = by_month.get(month_list[-3], 0) if len(month_list) > 2 else 0
        categories.append(
            {
                "category_id": str(cid) if cid else None, "name": cat.name if cat else "Uncategorised",
                "slug": cat.slug if cat else None, "total_minor": sum(by_month.values()), "series": series,
                "change_pct": round((latest - prev) / prev * 100, 1) if prev else None,
            }
        )  # fmt: skip
    categories.sort(key=lambda c: -int(c["total_minor"]))
    top_merchants = sorted(merchants.values(), key=lambda r: -r[1])[:top]
    return {
        "months": month_list,
        "total_by_month": [{"month": m, "total_minor": totals.get(m, 0)} for m in month_list],
        "categories": categories,
        "top_merchants": [
            {"merchant": r[0], "total_minor": r[1], "count": r[2], "last_on": r[3]} for r in top_merchants
        ],
    }

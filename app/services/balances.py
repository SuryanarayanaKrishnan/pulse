"""Live account balances: an anchor (user-entered or from the latest statement) plus later transactions.

Bank, cash and wallet accounts hold money (credits add, debits subtract); a credit-card balance is the
amount owed (debits add, credits subtract). An account with no anchor and no processed statement has an
unknown balance (``known=False``): we never guess it from a partial transaction history.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account, AccountType
from app.models.statement import ParseStatus, Statement
from app.models.transaction import Direction, Transaction
from app.services.parsers.dates import DUBAI


@dataclass(frozen=True, slots=True)
class Balance:
    account_id: UUID
    account_type: AccountType
    currency: str
    balance_minor: int | None  # money held, or amount owed for a credit card
    known: bool
    basis: str  # manual | statement | none
    as_of: datetime | None


async def _statement_anchors(session: AsyncSession, user_id: UUID) -> dict[UUID, tuple[int, datetime]]:
    """Latest processed statement per account that carries a closing balance."""
    rows = await session.execute(
        select(Statement)
        .where(
            Statement.user_id == user_id,
            Statement.deleted_at.is_(None),
            Statement.parse_status == ParseStatus.parsed,
            Statement.account_id.is_not(None),
            Statement.closing_balance_minor.is_not(None),
            Statement.period_end.is_not(None),
        )
        .order_by(Statement.period_end)
    )
    out: dict[UUID, tuple[int, datetime]] = {}
    for st in rows.scalars():
        assert (
            st.account_id is not None and st.period_end is not None and st.closing_balance_minor is not None
        )
        end = datetime(st.period_end.year, st.period_end.month, st.period_end.day, tzinfo=DUBAI) + timedelta(
            days=1
        )
        closing = st.closing_balance_minor
        out[st.account_id] = (abs(closing) if st.kind == "credit_card" else closing, end)
    return out


async def account_balances(session: AsyncSession, user_id: UUID) -> dict[UUID, Balance]:
    accounts = list(
        (
            await session.execute(
                select(Account).where(Account.user_id == user_id, Account.deleted_at.is_(None))
            )
        ).scalars()
    )
    statement_anchor = await _statement_anchors(session, user_id)
    out: dict[UUID, Balance] = {}
    for acc in accounts:
        if acc.balance_anchor_minor is not None and acc.balance_anchor_at is not None:
            anchor, at, basis = acc.balance_anchor_minor, acc.balance_anchor_at, "manual"
        elif acc.id in statement_anchor:
            (anchor, at), basis = statement_anchor[acc.id], "statement"
        else:
            out[acc.id] = Balance(acc.id, acc.type, acc.currency, None, False, "none", None)
            continue
        sign = case((Transaction.direction == Direction.credit, 1), else_=-1)
        net = (
            await session.execute(
                select(func.coalesce(func.sum(sign * Transaction.amount_minor), 0)).where(
                    Transaction.user_id == user_id,
                    Transaction.account_id == acc.id,
                    Transaction.deleted_at.is_(None),
                    Transaction.posted_at >= at,
                    Transaction.currency == acc.currency,
                )
            )
        ).scalar_one()
        delta = -int(net) if acc.type is AccountType.credit_card else int(net)
        out[acc.id] = Balance(acc.id, acc.type, acc.currency, anchor + delta, True, basis, at)
    return out

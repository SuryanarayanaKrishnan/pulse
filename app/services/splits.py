"""Pure split math: share allocation, member balances and debt simplification.

All amounts are integer minor units. Nothing here touches the database.
Sign convention for balances: positive = the member is owed money, negative = the member owes.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from uuid import UUID

from app.core.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class Transfer:
    from_member: UUID
    to_member: UUID
    amount_minor: int


@dataclass(frozen=True, slots=True)
class ExpenseRecord:
    paid_by: UUID
    amount_minor: int
    shares: Mapping[UUID, int]  # member -> share_minor, must sum to amount_minor


def split_equal(amount_minor: int, members: Sequence[UUID]) -> dict[UUID, int]:
    """Equal split; leftover fils go one each to the first members (deterministic)."""
    _require_positive(amount_minor)
    _require_unique(members)
    base, remainder = divmod(amount_minor, len(members))
    return {m: base + (1 if i < remainder else 0) for i, m in enumerate(members)}


def split_exact(amount_minor: int, exact: Mapping[UUID, int]) -> dict[UUID, int]:
    _require_positive(amount_minor)
    if not exact:
        raise DomainValidationError("At least one participant is required")
    if any(v < 0 for v in exact.values()):
        raise DomainValidationError("Exact amounts cannot be negative")
    total = sum(exact.values())
    if total != amount_minor:
        raise DomainValidationError(
            f"Exact shares sum to {total} but the expense is {amount_minor}",
            code="shares_mismatch",
        )
    return dict(exact)


def split_percent(amount_minor: int, percents: Mapping[UUID, Decimal]) -> dict[UUID, int]:
    """Percent split using the largest-remainder method so shares always sum to the total."""
    _require_positive(amount_minor)
    if not percents:
        raise DomainValidationError("At least one participant is required")
    if any(p < 0 for p in percents.values()):
        raise DomainValidationError("Percentages cannot be negative")
    if sum(percents.values()) != Decimal(100):
        raise DomainValidationError("Percentages must sum to 100", code="shares_mismatch")

    raw = {m: Decimal(amount_minor) * p / Decimal(100) for m, p in percents.items()}
    floored = {m: int(v.to_integral_value(rounding=ROUND_FLOOR)) for m, v in raw.items()}
    leftover = amount_minor - sum(floored.values())
    order = sorted(raw, key=lambda m: (-(raw[m] - floored[m]), list(raw).index(m)))
    for m in order[:leftover]:
        floored[m] += 1
    return floored


def compute_balances(
    expenses: Iterable[ExpenseRecord],
    settlements: Iterable[Transfer] = (),
    members: Iterable[UUID] = (),
) -> dict[UUID, int]:
    """Net position per member. Settlement from A to B means A paid B back."""
    net: dict[UUID, int] = defaultdict(int)
    for m in members:
        net[m] += 0
    for e in expenses:
        net[e.paid_by] += e.amount_minor
        for member, share in e.shares.items():
            net[member] -= share
    for s in settlements:
        net[s.from_member] += s.amount_minor
        net[s.to_member] -= s.amount_minor
    return dict(net)


def simplify_debts(balances: Mapping[UUID, int]) -> list[Transfer]:
    """Greedy debt simplification: repeatedly settle the largest debtor with the largest creditor.

    Produces at most (n - 1) transfers and never more than the naive pairwise ledger. Finding the
    true minimum is NP-hard (subset-sum); the greedy result is the standard practical choice.
    Ordering is deterministic (ties broken by id) so API responses are stable.
    """
    if sum(balances.values()) != 0:
        raise ValueError("Balances must sum to zero")

    cred = {m: b for m, b in balances.items() if b > 0}
    debt = {m: -b for m, b in balances.items() if b < 0}

    transfers: list[Transfer] = []
    while cred and debt:
        c = min(cred, key=lambda m: (-cred[m], str(m)))
        d = min(debt, key=lambda m: (-debt[m], str(m)))
        amount = min(cred[c], debt[d])
        transfers.append(Transfer(from_member=d, to_member=c, amount_minor=amount))
        cred[c] -= amount
        debt[d] -= amount
        if cred[c] == 0:
            del cred[c]
        if debt[d] == 0:
            del debt[d]
    return transfers


def _require_positive(amount: int) -> None:
    if amount <= 0:
        raise DomainValidationError("Amount must be positive")


def _require_unique(members: Sequence[UUID]) -> None:
    if not members:
        raise DomainValidationError("At least one participant is required")
    if len(set(members)) != len(members):
        raise DomainValidationError("Duplicate participants")

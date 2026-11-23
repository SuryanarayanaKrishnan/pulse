"""Balance reconciliation: does opening + signed(lines) equal the printed closing balance?

Balances follow the sign convention in ``statement_base`` (card: amount owed; account: funds held).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from app.models.transaction import Direction


@dataclass(frozen=True, slots=True)
class Reconciliation:
    status: Literal["matched", "mismatch", "pending"]
    diff_minor: int | None  # printed closing - computed closing; 0 when matched
    computed_closing_minor: int | None
    total_debits_minor: int
    total_credits_minor: int


def compute_reconciliation(
    kind: str, opening: int | None, closing: int | None, lines: Iterable[Any]
) -> Reconciliation:
    debits = credits_ = 0
    for ln in lines:
        if ln.direction == Direction.debit:
            debits += ln.amount_minor
        else:
            credits_ += ln.amount_minor
    if opening is None or closing is None:
        return Reconciliation("pending", None, None, debits, credits_)
    net = debits - credits_ if kind == "credit_card" else credits_ - debits
    computed = opening + net
    diff = closing - computed
    return Reconciliation("matched" if diff == 0 else "mismatch", diff, computed, debits, credits_)

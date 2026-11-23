"""Match statement lines against already-captured (SMS / notification / shortcut / manual) transactions.

A line matches a transaction when: same account (caller scopes), same direction, same amount
(either the account-currency amount or the foreign amount an SMS reported), posted within
``DATE_WINDOW_DAYS`` of each other, and the merchant strings are similar (rapidfuzz). Assignment is
greedy by best score so each transaction is claimed by at most one line.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from rapidfuzz import fuzz

from app.models.transaction import Direction
from app.services.merchants import clean_descriptor, match_entry
from app.services.parsers.dates import to_dubai_date

DATE_WINDOW_DAYS = 3
MIN_MERCHANT_SCORE = 60
# A lone same-amount candidate one day away is accepted on weaker merchant evidence.
WEAK_SCORE = 30


class LineLike(Protocol):
    posted_on: date
    description: str
    amount_minor: int
    currency: str
    direction: str
    foreign_amount_minor: int | None
    foreign_currency: str | None


class TxnLike(Protocol):
    amount_minor: int
    currency: str
    direction: Direction
    merchant_raw: str | None
    merchant_normalized: str | None

    @property
    def posted_at(self) -> object: ...


@dataclass(frozen=True, slots=True)
class Match:
    line_index: int
    txn_index: int
    score: int


def merchant_score(a: str | None, b: str | None) -> int | None:
    """0-100 similarity of two merchant descriptors; None when either side is empty."""
    if not a or not b:
        return None
    ea, eb = match_entry(a), match_entry(b)
    if ea is not None and ea is eb:
        return 100  # same dictionary merchant ("MAF CARREFOUR HYPER" == "Carrefour Moe")
    ca, cb = clean_descriptor(a).lower(), clean_descriptor(b).lower()
    if not ca or not cb:
        return None
    return int(max(fuzz.token_set_ratio(ca, cb), fuzz.partial_ratio(ca, cb) * 0.9))


def amounts_equal(line: LineLike, txn: TxnLike) -> bool:
    if txn.amount_minor == line.amount_minor and txn.currency == line.currency:
        return True
    return (
        line.foreign_amount_minor is not None
        and txn.amount_minor == line.foreign_amount_minor
        and txn.currency == line.foreign_currency
    )


def match_lines(lines: Sequence[LineLike], txns: Sequence[TxnLike]) -> list[Match]:
    pairs: list[tuple[int, int, int, int]] = []  # (-score, day_gap, line_idx, txn_idx)
    for li, line in enumerate(lines):
        feasible: list[tuple[int, int, int | None]] = []
        for ti, txn in enumerate(txns):
            if txn.direction.value != line.direction or not amounts_equal(line, txn):
                continue
            gap = abs((to_dubai_date(txn.posted_at) - line.posted_on).days)  # type: ignore[arg-type]
            if gap > DATE_WINDOW_DAYS:
                continue
            score = merchant_score(txn.merchant_normalized or txn.merchant_raw, line.description)
            feasible.append((ti, gap, score))
        for ti, gap, score in feasible:
            if score is not None and score >= MIN_MERCHANT_SCORE:
                accepted = score
            elif len(feasible) == 1 and gap <= 1 and (score is None or score >= WEAK_SCORE):
                accepted = score if score is not None else 40
            else:
                continue
            pairs.append((-accepted, gap, li, ti))
    pairs.sort()
    used_lines: set[int] = set()
    used_txns: set[int] = set()
    out: list[Match] = []
    for neg_score, _gap, li, ti in pairs:
        if li in used_lines or ti in used_txns:
            continue
        used_lines.add(li)
        used_txns.add(ti)
        out.append(Match(li, ti, -neg_score))
    return sorted(out, key=lambda m: m.line_index)

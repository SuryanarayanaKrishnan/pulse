import itertools
import random
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import DomainValidationError
from app.services.splits import (
    ExpenseRecord,
    Transfer,
    compute_balances,
    simplify_debts,
    split_equal,
    split_exact,
    split_percent,
)

A, B, C, D = (UUID(int=i) for i in range(1, 5))


def test_equal_split_distributes_remainder_deterministically():
    shares = split_equal(1000, [A, B, C])
    assert shares == {A: 334, B: 333, C: 333}
    assert sum(shares.values()) == 1000


def test_equal_split_rejects_duplicates_and_empty():
    with pytest.raises(DomainValidationError):
        split_equal(100, [A, A])
    with pytest.raises(DomainValidationError):
        split_equal(100, [])
    with pytest.raises(DomainValidationError):
        split_equal(0, [A])


def test_exact_split_must_sum():
    assert split_exact(100, {A: 30, B: 70}) == {A: 30, B: 70}
    with pytest.raises(DomainValidationError) as e:
        split_exact(100, {A: 30, B: 60})
    assert e.value.code == "shares_mismatch"
    with pytest.raises(DomainValidationError):
        split_exact(100, {A: 120, B: -20})


def test_percent_split_largest_remainder():
    shares = split_percent(100, {A: Decimal("33.33"), B: Decimal("33.33"), C: Decimal("33.34")})
    assert sum(shares.values()) == 100
    assert shares == {A: 33, B: 33, C: 34}


def test_percent_split_always_sums_to_total():
    rng = random.Random(7)
    for _ in range(200):
        total = rng.randint(1, 10_000_000)
        cuts = sorted(rng.sample(range(1, 10000), 3))
        pcts = [Decimal(b - a) / 100 for a, b in zip([0, *cuts], [*cuts, 10000], strict=True)]
        shares = split_percent(total, dict(zip([A, B, C, D], pcts, strict=True)))
        assert sum(shares.values()) == total


def test_percent_must_total_100():
    with pytest.raises(DomainValidationError):
        split_percent(100, {A: Decimal(50), B: Decimal(40)})


def test_balances_and_settlements():
    # A pays 300 for A,B,C equally; B pays 90 for B,C.
    e1 = ExpenseRecord(A, 300, split_equal(300, [A, B, C]))
    e2 = ExpenseRecord(B, 90, split_equal(90, [B, C]))
    bal = compute_balances([e1, e2])
    assert bal == {A: 200, B: -100 + 90 - 45, C: -100 - 45}
    assert sum(bal.values()) == 0

    # C pays A 145: C's debt reduces.
    bal2 = compute_balances([e1, e2], [Transfer(C, A, 145)])
    assert bal2[C] == 0
    assert bal2[A] == 55


def test_simplify_chain_collapses():
    # A owes B 10, B owes C 10  =>  A pays C 10 directly.
    bal = {A: -10, B: 0, C: 10}
    assert simplify_debts(bal) == [Transfer(A, C, 10)]


def test_simplify_basic_and_settled_state():
    assert simplify_debts({A: 0, B: 0}) == []
    t = simplify_debts({A: 70, B: -50, C: -20})
    assert {(x.from_member, x.to_member, x.amount_minor) for x in t} == {(B, A, 50), (C, A, 20)}


def test_simplify_rejects_unbalanced():
    with pytest.raises(ValueError):
        simplify_debts({A: 10, B: -5})


def test_simplify_property_transfers_reproduce_balances():
    rng = random.Random(42)
    for _ in range(300):
        n = rng.randint(2, 8)
        members = [uuid4() for _ in range(n)]
        vals = [rng.randint(-5000, 5000) for _ in range(n - 1)]
        vals.append(-sum(vals))
        bal = dict(zip(members, vals, strict=True))
        transfers = simplify_debts(bal)
        assert len(transfers) <= n - 1
        applied = dict.fromkeys(members, 0)
        for t in transfers:
            assert t.amount_minor > 0
            applied[t.from_member] += t.amount_minor
            applied[t.to_member] -= t.amount_minor
        # Executing the transfers brings everyone's net position to zero.
        assert all(bal[m] + applied[m] == 0 for m in members)


def test_simplify_is_order_independent():
    bal = {A: 100, B: -30, C: -40, D: -30}
    results = {
        tuple(simplify_debts(dict(p)))
        for p in itertools.islice(itertools.permutations(bal.items()), 12)
    }
    assert len(results) == 1

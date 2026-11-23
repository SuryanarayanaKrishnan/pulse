"""Net worth calculation. Pure functions over lightweight inputs (no ORM dependency)."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True, slots=True)
class AssetInput:
    kind: str
    currency: str
    manual_value_minor: int | None
    cost_basis_minor: int | None = None

    @property
    def value_minor(self) -> int:
        """Current value; falls back to cost basis when never revalued, else zero."""
        if self.manual_value_minor is not None:
            return self.manual_value_minor
        return self.cost_basis_minor or 0


@dataclass(frozen=True, slots=True)
class LiabilityInput:
    kind: str
    currency: str
    outstanding_minor: int


@dataclass(slots=True)
class NetWorth:
    base_currency: str
    total_assets_minor: int = 0
    total_liabilities_minor: int = 0
    assets_by_kind: dict[str, int] = field(default_factory=dict)
    liabilities_by_kind: dict[str, int] = field(default_factory=dict)
    # Currencies with no FX rate: excluded from totals and reported here, never silently dropped.
    unconverted_currencies: list[str] = field(default_factory=list)

    @property
    def net_worth_minor(self) -> int:
        return self.total_assets_minor - self.total_liabilities_minor


def convert_minor(amount_minor: int, rate: Decimal) -> int:
    return int((Decimal(amount_minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def compute_net_worth(
    assets: Iterable[AssetInput],
    liabilities: Iterable[LiabilityInput],
    base_currency: str = "AED",
    fx_rates: Mapping[str, Decimal] | None = None,
) -> NetWorth:
    """fx_rates maps currency -> units of base currency per 1 unit of that currency."""
    rates: dict[str, Decimal] = {base_currency: Decimal(1)}
    rates.update(fx_rates or {})
    result = NetWorth(base_currency=base_currency)
    missing: set[str] = set()
    a_kind: dict[str, int] = defaultdict(int)
    l_kind: dict[str, int] = defaultdict(int)

    for a in assets:
        if a.currency not in rates:
            missing.add(a.currency)
            continue
        a_kind[a.kind] += convert_minor(a.value_minor, rates[a.currency])
    for liab in liabilities:
        if liab.currency not in rates:
            missing.add(liab.currency)
            continue
        l_kind[liab.kind] += convert_minor(liab.outstanding_minor, rates[liab.currency])

    result.assets_by_kind = dict(a_kind)
    result.liabilities_by_kind = dict(l_kind)
    result.total_assets_minor = sum(a_kind.values())
    result.total_liabilities_minor = sum(l_kind.values())
    result.unconverted_currencies = sorted(missing)
    return result

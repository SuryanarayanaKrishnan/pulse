"""Valuation maths (pure): gold weight/purity to AED, indexed property value, and which value to show."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

AED_PER_USD_PEG = Decimal("3.6725")  # official fixed peg
GRAMS_PER_TROY_OZ = Decimal("31.1034768")
UNIT_GRAMS = {
    "g": Decimal(1),
    "gram": Decimal(1),
    "kg": Decimal(1000),
    "oz": GRAMS_PER_TROY_OZ,
    "tola": Decimal("11.6638038"),
}
STALE_AFTER = timedelta(days=3)
LIVE_WITHIN = timedelta(hours=24)


def purity_fraction(raw: Any) -> Decimal | None:
    """'22k' -> 22/24, '916' -> 0.916, 0.995 -> 0.995. None when not understood."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if m := re.fullmatch(r"(\d{1,2})\s*k(?:t|arat)?", text):
        k = int(m.group(1))
        return Decimal(k) / 24 if 1 <= k <= 24 else None
    if m := re.fullmatch(r"(?:1(?:\.0+)?|0?\.\d{2,4}|\d{3,4})", text):
        v = Decimal(text)
        v = v / 1000 if v > 1 else v
        return v if Decimal("0.1") <= v <= 1 else None
    return None


def gold_aed_per_gram(
    usd_per_oz: Decimal, aed_per_usd: Decimal = AED_PER_USD_PEG, purity: Decimal = Decimal(1)
) -> Decimal:
    return usd_per_oz * aed_per_usd / GRAMS_PER_TROY_OZ * purity


def to_minor(aed: Decimal) -> int:
    return int((aed * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True, slots=True)
class Valued:
    value_minor: int
    as_of: datetime  # when the market data behind it was published/fetched
    note: str


def value_gold(
    quantity: Decimal | None, unit: str | None, meta: dict[str, Any], usd_per_oz: Decimal | None,
    usd_as_of: datetime | None, aed_per_usd: Decimal,
) -> tuple[Valued | None, str]:  # fmt: skip
    """(valuation, reason-if-none). Spot value only: jewellery retail prices add making charges."""
    if quantity is None or quantity <= 0 or not unit:
        return None, "Add quantity and unit (g, kg, oz) to value this gold live"
    grams_per = UNIT_GRAMS.get(unit.strip().lower())
    if grams_per is None:
        return None, f"Unit '{unit}' is not supported for live gold (use g, kg, oz or tola)"
    purity = purity_fraction(meta.get("purity"))
    if purity is None:
        return None, "Add purity (e.g. 22k or 916) to value this gold live"
    if usd_per_oz is None or usd_as_of is None:
        return None, "No gold price available yet"
    grams = quantity * grams_per
    per_gram = gold_aed_per_gram(usd_per_oz, aed_per_usd, purity)
    note = f"{grams:.2f} g at AED {per_gram:.2f}/g spot (excludes making charges and dealer spread)"
    return Valued(to_minor(grams * per_gram), usd_as_of, note), ""


def _index_at(series: list[tuple[date, Decimal]], on: date) -> Decimal | None:
    """Index level on or before ``on`` (else the earliest point after it, only if within 60 days)."""
    before = [p for p in series if p[0] <= on]
    if before:
        return max(before, key=lambda p: p[0])[1]
    after = [p for p in series if p[0] > on and (p[0] - on).days <= 60]
    return min(after, key=lambda p: p[0])[1] if after else None


def value_property(
    meta: dict[str, Any], cost_basis_minor: int | None, acquired_on: date | None,
    series: list[tuple[date, Decimal]], fetched_at: datetime | None,
) -> tuple[Valued | None, str]:  # fmt: skip
    """base value x (latest index / index at the base date). Needs a base value, a base date and an index."""
    key = meta.get("index")
    if not key:
        return None, "Set metadata.index (e.g. dubai_apartments) to index this property"
    base = meta.get("base_value_minor", cost_basis_minor)
    raw_date = meta.get("base_date")
    try:
        base_date = date.fromisoformat(raw_date) if raw_date else acquired_on
    except ValueError:
        base_date = None
    if not isinstance(base, int) or base <= 0 or base_date is None:
        return None, "Add a base value and base date (or purchase price and date) to index this property"
    if not series or fetched_at is None:
        return None, f"No index data for '{key}'"
    latest_date, latest = max(series, key=lambda p: p[0])
    start = _index_at(series, base_date)
    if not start or start <= 0:
        return None, f"Index '{key}' has no value near {base_date.isoformat()}"
    value = to_minor(Decimal(base) / 100 * latest / start)
    note = (
        f"Indexed from {base_date.isoformat()} using '{key}' ({start:.1f} to {latest:.1f}, "
        f"index dated {latest_date.isoformat()}); an estimate, not a valuation"
    )
    return Valued(value, fetched_at, note), ""


@dataclass(frozen=True, slots=True)
class EffectiveValue:
    value_minor: int | None
    basis: str  # market | estimate | manual | cost_basis | unknown
    last_updated: datetime | None
    note: str | None


def effective_value(
    *, source: str, manual_minor: int | None, valued_at: datetime | None, market_minor: int | None,
    market_at: datetime | None, note: str | None, cost_basis_minor: int | None, now: datetime,
) -> EffectiveValue:  # fmt: skip
    """The automatic value while its data is fresh, otherwise the manual value (graceful fallback)."""
    if source in ("market", "estimate"):
        if market_minor is not None and market_at is not None and now - market_at <= STALE_AFTER:
            return EffectiveValue(market_minor, source, market_at, note)
        why = "stale" if market_minor is not None else "not available yet"
        fallback = f"Live valuation {why}"
        if note and market_minor is None:
            fallback = note
        if manual_minor is not None:
            return EffectiveValue(manual_minor, "manual", valued_at, f"{fallback}; showing your manual value")
        if cost_basis_minor is not None:
            return EffectiveValue(
                cost_basis_minor, "cost_basis", None, f"{fallback}; showing the purchase price"
            )
        return EffectiveValue(None, "unknown", None, fallback)
    if manual_minor is not None:
        return EffectiveValue(manual_minor, "manual", valued_at, None)
    if cost_basis_minor is not None:
        return EffectiveValue(
            cost_basis_minor, "cost_basis", None, "No value entered; showing the purchase price"
        )
    return EffectiveValue(None, "unknown", None, "No value entered")


def value_priced(
    quantity: Decimal | None,
    key: str,
    hint: str,
    quote: tuple[Decimal, datetime, str] | None,
    aed_per_usd: Decimal,
) -> tuple[Valued | None, str]:
    """quantity x USD price x AED/USD for a crypto coin or a US stock."""
    if not key:
        return None, f"Set {hint} to value this live"
    if quantity is None or quantity <= 0:
        return None, "Add a quantity to value this live"
    if quote is None:
        return None, f"No price cached yet for '{key}'"
    price, at, source = quote
    value = to_minor(quantity * price * aed_per_usd)
    return Valued(value, at, f"{quantity:f} x USD {price:f} ({source}) at {aed_per_usd:f} AED/USD"), ""

"""Amount parsing for statements: '1,234.50', '(12.00)', '-5.00', '250.00 Cr', 'AED 10.00'."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

AMOUNT_RE = r"-?\(?\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?(?:\s?(?:CR|DR|Cr|Dr|cr|dr))?|-?\(?\d+\.\d{2}\)?(?:\s?(?:CR|DR|Cr|Dr|cr|dr))?"
_TOKEN = re.compile(
    r"^(?P<neg>-)?(?P<paren>\()?(?P<num>[\d,]+\.\d{2})\)?(?:\s?(?P<mark>CR|DR))?$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class Money:
    minor: int  # absolute value in minor units
    negative: bool  # leading '-' or parentheses
    marker: str | None  # "CR" / "DR" suffix, upper-cased


def parse_money(token: str) -> Money | None:
    t = token.strip().replace("−", "-")
    t = re.sub(r"^(?:AED|Dhs?\.?)\s*", "", t, flags=re.IGNORECASE)
    m = _TOKEN.match(t)
    if not m:
        return None
    try:
        minor = int((Decimal(m["num"].replace(",", "")) * 100).to_integral_value())
    except InvalidOperation:
        return None
    return Money(minor, bool(m["neg"] or m["paren"]), m["mark"].upper() if m["mark"] else None)


def to_minor(token: str) -> int | None:
    m = parse_money(token)
    return m.minor if m else None

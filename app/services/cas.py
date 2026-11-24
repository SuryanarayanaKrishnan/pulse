"""Best-effort parser for CAMS / KFintech consolidated account statements (CAS), from extracted text.

Layout assumed (detailed statement)::

    Folio No: 12345678 / 90
    Example Mutual Fund
    EX12-Example Flexi Cap Fund - Direct Plan - Growth ISIN: INF000A01AB1(Advisor: DIRECT) Registrar : CAMS
    Opening Unit Balance: 0.000
    15-Jan-2024 Purchase 5,000.00 25.123 199.0160 25.123
    10-Mar-2024 Redemption (2,000.00) (9.500) 210.5263 40.423
    Closing Unit Balance: 40.423
    NAV on 31-Mar-2024: INR 215.1000 Total Cost Value: 8,000.00 Market Value on 31-Mar-2024: INR 8,695.19

Transaction lines are ``date description amount units price balance``. Anything that does not fit is skipped
and reported in ``warnings``; the user's PAN is never extracted. No network, no storage.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.models.portfolio import FundTxnKind

NUM = r"\(?-?[\d,]+(?:\.\d+)?\)?"
DATE = r"\d{1,2}-[A-Za-z]{3}-\d{4}"
TXN_RE = re.compile(rf"^({DATE})\s+(.+?)\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s*$")
FOLIO_RE = re.compile(r"Folio\s*No\s*:\s*([A-Za-z0-9/ ]+?)(?:\s{2,}|\s+PAN\b|\s+KYC\b|$)", re.I)
ISIN_RE = re.compile(r"ISIN\s*:\s*(INF[A-Z0-9]{9})", re.I)
CLOSING_RE = re.compile(r"Closing\s+Unit\s+Balance\s*:\s*([\d,]+(?:\.\d+)?)", re.I)
NAV_RE = re.compile(rf"NAV\s+on\s+({DATE})\s*:\s*(?:INR|Rs\.?)?\s*([\d,]+(?:\.\d+)?)", re.I)
COST_RE = re.compile(r"Total\s+Cost\s+Value\s*:\s*(?:INR|Rs\.?)?\s*([\d,]+(?:\.\d+)?)", re.I)


@dataclass(slots=True)
class CasTxn:
    txn_date: date
    kind: FundTxnKind
    description: str
    amount_minor: int  # signed, INR paise
    units: Decimal  # signed
    nav: Decimal | None

    @property
    def dedupe_hash(self) -> str:
        raw = f"{self.txn_date}|{self.kind}|{self.units}|{self.amount_minor}|{self.nav}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(slots=True)
class CasHolding:
    folio: str
    amc: str | None
    name: str
    isin: str | None
    closing_units: Decimal | None = None
    nav: Decimal | None = None
    nav_date: date | None = None
    total_cost_minor: int | None = None
    transactions: list[CasTxn] = field(default_factory=list)

    @property
    def units(self) -> Decimal:
        return (
            self.closing_units
            if self.closing_units is not None
            else sum((t.units for t in self.transactions), Decimal(0))
        )

    @property
    def avg_cost_per_unit(self) -> Decimal | None:
        if self.total_cost_minor and self.units > 0:
            return (Decimal(self.total_cost_minor) / 100 / self.units).quantize(Decimal("0.0001"))
        return None


@dataclass(slots=True)
class CasResult:
    holdings: list[CasHolding]
    warnings: list[str]


def _num(raw: str) -> Decimal:
    neg = raw.startswith("(") or raw.startswith("-")
    try:
        v = Decimal(raw.strip("()-").replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(raw) from exc
    return -v if neg else v


def _date(raw: str) -> date:
    return datetime.strptime(raw.title(), "%d-%b-%Y").date()


def classify(description: str) -> FundTxnKind:
    d = description.lower()
    if "switch" in d:
        return FundTxnKind.switch_out if "out" in d else FundTxnKind.switch_in
    if "redemption" in d or "redeem" in d:
        return FundTxnKind.redemption
    if "dividend" in d:
        return FundTxnKind.dividend
    if "sip" in d or "systematic" in d:
        return FundTxnKind.sip
    if "purchase" in d or "subscription" in d:
        return FundTxnKind.purchase
    return FundTxnKind.other


def _clean_name(line: str) -> str:
    name = ISIN_RE.split(line)[0]
    name = re.sub(r"^[A-Z0-9]{2,10}-(?=\S)", "", name.strip())
    return re.sub(r"\s+", " ", name).strip(" -")


def parse_cas(text: str) -> CasResult:
    holdings: list[CasHolding] = []
    warnings: list[str] = []
    folio: str | None = None
    amc: str | None = None
    cur: CasHolding | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := FOLIO_RE.search(line):
            folio, cur = re.sub(r"\s+", "", m.group(1)), None
            continue
        if re.search(r"mutual fund\s*$", line, re.I) and not ISIN_RE.search(line):
            amc = line
            continue
        if ISIN_RE.search(line) and folio:
            cur = CasHolding(folio, amc, _clean_name(line), ISIN_RE.search(line).group(1).upper())  # type: ignore[union-attr]
            holdings.append(cur)
            continue
        if cur is None:
            continue
        if m := TXN_RE.match(line):
            try:
                kind = classify(m.group(2))
                amount, units, price = _num(m.group(3)), _num(m.group(4)), _num(m.group(5))
                if kind in (FundTxnKind.redemption, FundTxnKind.switch_out):
                    amount, units = -abs(amount), -abs(units)
                cur.transactions.append(
                    CasTxn(
                        _date(m.group(1)),
                        kind,
                        m.group(2).strip(),
                        int((amount * 100).to_integral_value()),
                        units,
                        price,
                    )
                )
            except ValueError:
                warnings.append(f"Skipped unreadable line: {line[:80]}")
            continue
        if m := CLOSING_RE.search(line):
            cur.closing_units = _num(m.group(1))
        if m := NAV_RE.search(line):
            cur.nav_date, cur.nav = _date(m.group(1)), _num(m.group(2))
        if m := COST_RE.search(line):
            cur.total_cost_minor = int((_num(m.group(1)) * 100).to_integral_value())
    for h in holdings:
        if h.closing_units is None:
            warnings.append(f"No closing balance for {h.name}; units summed from transactions")
    if not holdings:
        warnings.append("No schemes recognised: is this a CAMS/KFintech detailed CAS?")
    return CasResult(holdings, warnings)

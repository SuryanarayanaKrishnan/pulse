"""Salary detection from transaction descriptors and the recurring-salary summary.

Detection is deliberately conservative: a *credit* whose descriptor carries a salary marker
("SALARY", "PAYROLL", "WPS" / "Wage Protection System", "SAL CR") and is not an advance/loan/reversal.
Every match becomes one ``salary_records`` row per transaction (idempotent). "Recurring" is derived
afterwards from the records: two or more distinct months => a salary stream.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import SalaryRecord
from app.models.transaction import Direction, Transaction
from app.services.categorisation import default_category_ids, iter_transactions
from app.services.parsers.dates import to_dubai_date

MIN_SALARY_MINOR = 50_000  # AED 500: below this a "salary" credit is almost certainly a fee refund etc.

# (name, pattern) in priority order; the first that matches names the detection.
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wps", re.compile(r"\bWPS\b|WAGE\s+PROTECTION")),
    ("payroll", re.compile(r"\bPAY\s?ROLL\b")),
    ("salary", re.compile(r"\bSALAR(?:Y|IES)\b|\bSAL\.?\s?(?:CR\b|CREDIT)|\bSALARY\b")),
)
_NOT_SALARY = re.compile(
    r"\b(?:ADVANCE|LOAN|REVERS(?:AL|ED)|REFUND|FEE|CHARGE|CERTIFICATE|RETURN(?:ED)?|REJECT(?:ED)?)\b"
)
_NOISE = re.compile(
    r"\b(?:WPS|PAY\s?ROLL|SALAR(?:Y|IES)|SAL|CR|CREDIT(?:ED)?|TRANSFER|TRF|PAYMENT|PMT|DEPOSIT|FOR|THE|MONTH|OF|"
    r"FROM|FRM|BY|REF|REFERENCE|NO|WAGE|PROTECTION|SYSTEM|INWARD|LOCAL|IPI|ACCOUNT|A/C|AC|ADCB|ENBD|FAB|MASHREQ)\b"
)
_MONTH = re.compile(
    r"\b(?:JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|"
    r"SEP(?:T(?:EMBER)?)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)\b"
)
_AFTER_FROM = re.compile(r"\b(?:FROM|FRM|BY)\b\s*(.+)$")


@dataclass(frozen=True, slots=True)
class SalaryMatch:
    matched_by: str
    employer: str | None


_STOP = re.compile(r"\b(?:ON|AT|REF|DATED|VALUE|AVL|AVAILABLE|BAL(?:ANCE)?|TO|YOUR|ACCOUNT)\b|[.,;]\s|\.$")
_ACRONYMS = {"LLC", "FZE", "FZ", "FZCO", "FZ-LLC", "PJSC", "LTD", "PLC", "UAE", "DMCC", "WLL", "LLP", "INC"}


def _employer(text: str, *, allow_bare: bool) -> str | None:
    """Employer name from a descriptor. With ``allow_bare`` the text left after dropping markers, months,
    dates and references is taken (a bank descriptor); otherwise only what follows "FROM" (free-form SMS)."""
    upper = re.sub(r"\s+", " ", text.upper()).strip()
    m = _AFTER_FROM.search(upper)
    if m:
        candidate = _STOP.split(m.group(1))[0]
    elif allow_bare:
        candidate = upper
    else:
        return None
    candidate = _MONTH.sub(" ", candidate)
    candidate = re.sub(r"\b(?:19|20)\d{2}\b|\b\d{2}[/.-]\d{2}[/.-]\d{2,4}\b|[#*]?\b\d{4,}\b", " ", candidate)
    candidate = _NOISE.sub(" ", candidate)
    candidate = re.sub(r"[^A-Z&.'() ]+", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .-&")
    if len(re.sub(r"[^A-Z]", "", candidate)) < 3:
        return None
    words = [w if w in _ACRONYMS or len(w) <= 3 else w.title() for w in candidate.split()]
    return " ".join(words)[:160]


def detect_salary(
    description: str | None,
    direction: Direction | None,
    amount_minor: int | None,
    sms_text: str | None = None,
) -> SalaryMatch | None:
    """Is this credit a salary payment? ``description`` is the bank descriptor / statement line, ``sms_text``
    the full message when it came from an SMS (often "Salary credited..." with no merchant at all)."""
    if direction is not Direction.credit:
        return None
    if amount_minor is not None and amount_minor < MIN_SALARY_MINOR:
        return None
    text = re.sub(r"\s+", " ", " ".join(filter(None, [description, sms_text])).upper())
    if not text or _NOT_SALARY.search(text):
        return None
    for name, pattern in _MARKERS:
        if pattern.search(text):
            employer = _employer(description, allow_bare=True) if description else None
            return SalaryMatch(
                name, employer or (_employer(sms_text, allow_bare=False) if sms_text else None)
            )
    return None


async def record_salary(session: AsyncSession, txn: Transaction, sms_text: str | None = None) -> bool:
    """Create the salary record for a freshly stored credit if it looks like a salary. True if created.

    Idempotent per transaction; the transaction must already be flushed (it needs an id).
    """
    description = " ".join(filter(None, [txn.merchant_raw, txn.notes])) or None
    match = detect_salary(description, txn.direction, txn.amount_minor, sms_text)
    if match is None:
        return False
    if txn.category_id is None:  # e.g. an SMS that only says "salary credited": file it under Salary
        txn.category_id = (await default_category_ids(session)).get("salary")
    result = await session.execute(
        pg_insert(SalaryRecord)
        .values(
            user_id=txn.user_id,
            account_id=txn.account_id,
            transaction_id=txn.id,
            employer=match.employer,
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            received_on=to_dubai_date(txn.posted_at),
            matched_by=match.matched_by,
        )
        .on_conflict_do_nothing(constraint="uq_salary_records_transaction_id")
        .returning(SalaryRecord.id)
    )
    return result.scalar_one_or_none() is not None


async def rescan(session: AsyncSession, user_id: UUID) -> tuple[int, int]:
    """Look through the user's past credits. Returns (credits scanned, new salary records)."""
    scanned = created = 0
    async for txn in iter_transactions(session, user_id):
        if txn.direction is not Direction.credit:
            continue
        scanned += 1
        if await record_salary(session, txn):
            created += 1
    return scanned, created


async def user_records(session: AsyncSession, user_id: UUID) -> list[SalaryRecord]:
    rows = await session.execute(
        select(SalaryRecord)
        .outerjoin(Transaction, Transaction.id == SalaryRecord.transaction_id)
        .where(
            SalaryRecord.user_id == user_id,
            SalaryRecord.deleted_at.is_(None),
            # a record follows its transaction: deleted transactions (or statements) take it with them
            Transaction.deleted_at.is_(None),
        )
        .order_by(SalaryRecord.received_on.desc(), SalaryRecord.id)
    )
    return list(rows.scalars())


# ---------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Payment:
    received_on: date
    amount_minor: int
    employer: str | None
    currency: str


@dataclass(frozen=True, slots=True)
class Summary:
    currency: str | None
    months_observed: int
    is_recurring: bool
    average_monthly_minor: int | None
    typical_day: int | None
    next_expected_on: date | None
    latest_month_minor: int | None
    previous_month_minor: int | None
    monthly: list[tuple[date, int]]  # (first of month, total), newest first
    employers: list[tuple[str | None, int, int]]  # (employer, payments, latest amount), most recent first


def _clamp_day(year: int, month: int, day: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return date(year, month, min(day, last))


def summarise(payments: list[Payment], today: date, *, average_over: int = 3) -> Summary:
    """Recurring-salary summary over the payments of the user's dominant currency (pure)."""
    if not payments:
        return Summary(None, 0, False, None, None, None, None, None, [], [])
    currency = Counter(p.currency for p in payments).most_common(1)[0][0]
    pays = sorted((p for p in payments if p.currency == currency), key=lambda p: p.received_on, reverse=True)
    totals: dict[date, int] = defaultdict(int)
    for p in pays:
        totals[p.received_on.replace(day=1)] += p.amount_minor
    monthly = sorted(totals.items(), reverse=True)
    recent = [t for _, t in monthly[:average_over]]
    by_employer: dict[str | None, list[Payment]] = defaultdict(list)
    for p in pays:
        by_employer[p.employer].append(p)
    main = max(by_employer.values(), key=lambda ps: (len(ps), ps[0].received_on))
    typical_day = int(median(p.received_on.day for p in main[:6]))
    latest_month = monthly[0][0]
    candidate = _clamp_day(
        latest_month.year + (latest_month.month == 12), latest_month.month % 12 + 1, typical_day
    )
    recurring = len(monthly) >= 2
    next_expected = candidate if recurring and candidate >= today - timedelta(days=10) else None
    return Summary(
        currency=currency,
        months_observed=len(monthly),
        is_recurring=recurring,
        average_monthly_minor=round(sum(recent) / len(recent)),
        typical_day=typical_day,
        next_expected_on=next_expected,
        latest_month_minor=monthly[0][1],
        previous_month_minor=monthly[1][1] if len(monthly) > 1 else None,
        monthly=monthly,
        employers=[
            (e, len(ps), ps[0].amount_minor)
            for e, ps in sorted(by_employer.items(), key=lambda kv: kv[1][0].received_on, reverse=True)
        ],
    )

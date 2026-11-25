"""Payment-due notices sent as plain emails (no PDF): due date, minimum due and statement amount.

Tabby Card sends "Your Tabby Card payment is due in N days / <from> - <to> . Due on 3 December /
A no-fee minimum amount of 2,341.75 ... / Statement amount 2,390.13". Amounts carry no currency (AED).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

from app.services.parsers.dates import _MONTHS

_WS = re.compile(r"\s+")
_DUE = re.compile(r"due on (\d{1,2}) ([A-Za-z]{3,9})(?:,? (\d{4}))?", re.I)
_MIN = re.compile(r"no-fee minimum amount of\s+(\d[\d,]*(?:\.\d{1,2})?)", re.I)
_STATEMENT = re.compile(r"(\d[\d,]*(?:\.\d{1,2})?)\s+statement amount", re.I)


@dataclass(frozen=True, slots=True)
class DueNotice:
    institution: str
    due_date: date
    min_due_minor: int
    statement_minor: int | None = None


def _minor(s: str) -> int:
    whole, _, frac = s.replace(",", "").partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def parse_tabby_card_due(text: str, *, received_at: datetime) -> DueNotice | None:
    """The year is not printed: pick the next occurrence of that day on/after the email date."""
    t = _WS.sub(" ", text)
    due, minimum = _DUE.search(t), _MIN.search(t)
    if not due or not minimum or (month := _MONTHS.get(due[2][:3].lower())) is None:
        return None
    year = int(due[3]) if due[3] else received_at.year
    try:
        due_date = date(year, month, int(due[1]))
    except ValueError:
        return None
    if not due[3] and due_date < received_at.date():
        due_date = date(year + 1, month, int(due[1]))
    stmt = _STATEMENT.search(t)
    return DueNotice("tabby", due_date, _minor(minimum[1]), _minor(stmt[1]) if stmt else None)

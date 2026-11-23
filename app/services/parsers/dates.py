"""Date helpers shared by SMS and statement parsers. UAE formats are day-first."""

import re
from datetime import UTC, date, datetime, timedelta, timezone

# UAE has no DST, so a fixed offset is exact.
DUBAI = timezone(timedelta(hours=4), "Asia/Dubai")

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}

# Pattern fragment matching every supported format (no capture groups; use parse_date on the match).
DATE_RE = (
    r"(?:\d{4}-\d{2}-\d{2}"  # 2026-03-12
    r"|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"  # 12/03/2026, 12-03-26, 12.03.2026
    r"|\d{1,2}[\s\-]?[A-Za-z]{3,9}[\s\-,]*\d{2,4})"  # 12 Mar 2026, 12-Mar-26, 12MAR26, 12 March, 2026
)
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$")
_NAMED = re.compile(r"^(\d{1,2})[\s\-]?([A-Za-z]{3,9})[\s\-,]*(\d{2,4})$")


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if len(y) == 2 else n


def parse_date(value: str) -> date | None:
    s = value.strip()
    try:
        if m := _ISO.match(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _NUMERIC.match(s):
            return date(_year(m[3]), int(m[2]), int(m[1]))
        if m := _NAMED.match(s):
            month = _MONTHS.get(m[2][:3].lower())
            if month:
                return date(_year(m[3]), month, int(m[1]))
    except ValueError:
        return None
    return None


def to_dubai_date(dt: datetime) -> date:
    return dt.astimezone(DUBAI).date() if dt.tzinfo else dt.date()


def at_date(d: date, near: datetime | None = None) -> datetime:
    """A timestamp on day ``d`` (Dubai): keep ``near``'s time when it is the same day, else noon."""
    if near is not None and to_dubai_date(near) == d:
        return near if near.tzinfo else near.replace(tzinfo=UTC)
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=DUBAI)

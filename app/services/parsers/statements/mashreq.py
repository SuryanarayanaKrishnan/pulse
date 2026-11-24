"""Mashreq statements.

Cards (documented layout, unverified against a real card PDF): credits are printed as negative amounts
("-500.00") instead of "Cr"; summary labels "Previous Balance" / "Statement Balance".

Accounts (verified on real NEO "Consolidated Relationship Statement" PDFs, see docs/STATEMENT_LAYOUTS.md):
one PDF holds one section per account (current, savings, ...). Each section repeats a page header, then the
table ``Date | Transaction | Reference no | Debit | Credit | Balance`` where

* amounts carry three decimals (``6,000.000``) and every row prints Debit, Credit and Balance;
* the description is a vertically centred multi-line cell: wrapped text appears *above and below* the
  dated row, and filler lines ending ``0.000 0.000 0.000`` carry description text too;
* ``OPENING BALANCE`` / ``CLOSING BALANCE`` bracket the section, ``Balance carried forward`` /
  ``Balance brought forward`` bridge page breaks;
* the Arabic column headers extract as garbage and are dropped.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from app.models.transaction import Direction
from app.services.parsers.dates import parse_date
from app.services.parsers.statement_base import (
    ParsedStatement,
    StatementDocument,
    StatementKind,
    StatementLineData,
)
from app.services.parsers.statements.layout import Labels, Layout, LayoutStatementParser
from app.services.parsers.statements.money import AMOUNT3_RE, parse_money3

CARD = Layout(
    has_balance_column=False,
    labels=Labels(closing=(r"statement balance", r"total amount due", r"closing balance")),
)
ACCOUNT = Layout()

_M3 = rf"(?:{AMOUNT3_RE})"
_TITLE = re.compile(r"^(?P<type>[A-Za-z]+) Account Statement\b")
_TABLE_HEADER = re.compile(r"^Date Transaction Reference no Debit Credit Balance$")
_ACCT = re.compile(r"^Account No\s+(?P<acct>\d{6,20})\s+(?P<ccy>[A-Z]{3})\b")
_IBAN = re.compile(r"^IBAN\s+(?P<iban>AE\d{2}\d{19})\b")
_PERIOD = re.compile(r"^Statement for Period\s+(?P<a>\d{2}/\d{2}/\d{4})\s+to\s+(?P<b>\d{2}/\d{2}/\d{4})")
_ISSUE = re.compile(r"^Issue Date\s+(?P<d>\d{2}/\d{2}/\d{4})")
_PAGE = re.compile(r"^Page \d+ of \d+$")
_OPENING = re.compile(rf"^OPENING BALANCE(?:\s+{_M3}){{2}}\s+(?P<bal>{_M3})$")
_CLOSING = re.compile(rf"^CLOSING BALANCE(?:\s+{_M3}){{2}}\s+(?P<bal>{_M3})$")
_CARRY = re.compile(rf"^Balance (?P<kind>carried|brought) forward\s+(?P<bal>{_M3})$", re.I)
_ROW = re.compile(
    rf"^(?P<date>\d{{2}}/\d{{2}}/\d{{4}})\s*(?P<mid>.*?)\s+(?P<d>{_M3})\s+(?P<c>{_M3})\s+(?P<b>{_M3})$"
)
_ZERO_LINE = re.compile(r"^(?P<text>.*?)\s*0\.000\s+0\.000\s+0\.000$")
_REF = re.compile(r"(?:^|\s)(?P<ref>\d{3}[A-Z]{4}[A-Za-z0-9]{3,}(?: \d{5})?)$")
_VALUE_DATE = re.compile(r"VALUE DATE:\s*\d{2}-\d{2}-\d{4}\s*", re.I)
_STARTER = re.compile(r"^(?:VALUE DATE:|IPP TRANSFER\b|QuickRemit\b|Cardless Cash\b)", re.I)
_CONTINUES = re.compile(r"^(?:ACCOUNT No:|Transaction Ref#|T_[0-9a-f]{8,})", re.I)
_SUMMARY_ROW = re.compile(
    rf"^(?:(?P<product>.*?)\s+)?(?P<acct>\d{{6,20}})\s+(?P<ccy>[A-Z]{{3}})\s+{_M3}\s+(?P<bal>{_M3})\s+(?P<avg>{_M3})"
)
_HOLDER = re.compile(r"^[A-Z][A-Z.'-]+(?: [A-Z][A-Z.'-]+)+$")

# Reference-code family (characters 4-7 of the "Reference no") -> label, used when a row has no text at all.
_REF_KIND = {
    "AACT": "Account to Account Transfer",
    "IPPD": "IPP Transfer",
    "WPSP": "Salary Received",
    "IWRT": "Inward Remittance",
    "CLCH": "Cardless Cash withdrawal",
    "FSBI": "QuickRemit",
    "DCRD": "Local Fund Transfer",
    "MCIT": "Interest",
}


_ANCHOR = re.compile(
    r"^(?:[A-Za-z]+ Account Statement|Account No\s+\d|IBAN\s+AE|Customer Number|Issue Date|Statement (?:for )?Period)"
)


def _garbled(line: str) -> bool:
    """Extraction noise from the Arabic header text: cid escapes or mostly non-ASCII characters."""
    if _ANCHOR.match(line):
        return False  # English label followed by its Arabic twin: keep, the regexes ignore the tail
    if "(cid:" in line:
        return True
    odd = sum(1 for ch in line if ord(ch) > 126 or (ord(ch) < 32))
    return odd >= 2 and odd / max(len(line), 1) > 0.15


@dataclass
class _Row:
    posted_on: date
    mid: str
    ref: str | None
    debit: int
    credit: int
    balance: int
    lead: list[str] = field(default_factory=list)
    trail: list[str] = field(default_factory=list)


@dataclass
class _Section:
    account: str
    currency: str = "AED"
    iban: str | None = None
    product: str | None = None
    period: tuple[date, date] | None = None
    opening: int | None = None
    closing: int | None = None
    rows: list[_Row] = field(default_factory=list)


def _split_gap(gap: list[tuple[str, bool]], lead_count: int) -> int:
    """Index where ``gap`` (text lines between two dated rows) stops belonging to the first row.

    Lines before it are the first row's wrapped description, lines from it on lead the second row.
    Rules, in order: never split before the last filler line (``... 0.000 0.000 0.000``) or the
    continuation lines that follow it; split before the first known entry starter; otherwise use the
    centring of the first row (it printed ``lead_count`` lines above its dated line, so it prints that
    many or one more below) and, with no lines above, assume it has none below either.
    """
    lines = [t for t, _ in gap]
    lb = 0
    for i, (_, zero) in enumerate(gap):
        if zero:
            lb = i + 1
    while lb < len(lines) and _CONTINUES.match(lines[lb]):
        lb += 1
    k = min(len(lines), lead_count + 1) if lead_count else 0
    for i, ln in enumerate(lines):
        if i >= lb and _STARTER.match(ln):
            k = i
            break
    else:
        if lb:
            k = lb
    while k < len(lines) and _CONTINUES.match(lines[k]):  # a hash/"ACCOUNT No:" line never starts a row
        k += 1
    return k


def _description(row: _Row) -> str:
    parts = [*row.lead, row.mid, *row.trail]
    text = _VALUE_DATE.sub("", " ".join(p for p in parts if p))
    text = re.sub(r"\s+", " ", text).strip()
    if not text and row.ref:
        text = _REF_KIND.get(row.ref[3:7], "Transfer")
    return text


def _parse_holder(lines: list[str]) -> str | None:
    for i, ln in enumerate(lines[:12]):
        if _HOLDER.match(ln) and "STATEMENT" not in ln:
            return ln
        if i > 0 and ln.startswith("Customer Number"):
            break
    return None


def _parse_summary(lines: list[str]) -> dict[str, str]:
    """Account number -> product name from the "... Account Summary" tables on page 1."""
    out: dict[str, str] = {}
    for i, ln in enumerate(lines):
        m = _SUMMARY_ROW.match(ln)
        if not m or m["acct"] in out:
            continue
        product = (m["product"] or "").strip()
        if not product:
            prev = lines[i - 1] if i else ""
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            prev = "" if (_garbled(prev) or "Balance" in prev or "Account Type" in prev) else prev
            nxt = (
                nxt if (nxt and not nxt.startswith(("IBAN", "Savings", "Current")) and len(nxt) < 20) else ""
            )
            product = f"{prev} {nxt}".strip()
        if product and not _garbled(product):
            out[m["acct"]] = product
    return out


def parse_account_sections(text: str) -> tuple[list[ParsedStatement], date | None]:
    """All account sections of a NEO-style consolidated PDF, each as its own ParsedStatement."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not _garbled(ln)]
    holder = _parse_holder(lines)
    products = _parse_summary(lines)
    issue = next((parse_date(m["d"]) for ln in lines if (m := _ISSUE.match(ln))), None)

    sections: dict[str, _Section] = {}
    warnings: dict[str, list[str]] = {}
    order: list[str] = []
    cur: _Section | None = None
    pending: _Section | None = None  # header seen, table not started yet
    in_rows = False
    prev_row: _Row | None = None
    gap: list[tuple[str, bool]] = []

    def flush_boundary() -> None:
        nonlocal prev_row, gap
        if prev_row is not None:
            prev_row.trail.extend(t for t, _ in gap)
        prev_row, gap = None, []

    for ln in lines:
        if _PAGE.match(ln):
            flush_boundary()
            in_rows = False
            continue
        if (tm := _TITLE.match(ln)) and tm["type"].lower() != "consolidated":
            flush_boundary()
            in_rows = False
            pending = _Section(account="")
            continue
        if pending is not None and not in_rows:
            if am := _ACCT.match(ln):
                pending.account, pending.currency = am["acct"], am["ccy"]
            elif im := _IBAN.match(ln):
                pending.iban = im["iban"]
            elif pm := _PERIOD.match(ln):
                a, b = parse_date(pm["a"]), parse_date(pm["b"])
                if a and b:
                    pending.period = (a, b)
            elif _TABLE_HEADER.match(ln) and pending.account:
                key = pending.account
                if key not in sections:
                    pending.product = products.get(key)
                    sections[key] = pending
                    warnings[key] = []
                    order.append(key)
                else:  # next page of a section we already know
                    sections[key].iban = sections[key].iban or pending.iban
                cur, in_rows, pending = sections[key], True, None
            continue
        if not in_rows or cur is None:
            continue
        if m := _OPENING.match(ln):
            flush_boundary()
            cur.opening = parse_money3(m["bal"])
            continue
        if m := _CLOSING.match(ln):
            flush_boundary()
            cur.closing = parse_money3(m["bal"])
            continue
        if m := _CARRY.match(ln):
            flush_boundary()
            continue
        if m := _ROW.match(ln):
            dt = parse_date(m["date"])
            mid = m["mid"].strip()
            ref = None
            if rm := _REF.search(mid):
                ref, mid = rm["ref"], mid[: rm.start()].strip()
            if dt is None:
                continue
            row = _Row(
                dt, mid, ref, parse_money3(m["d"]) or 0, parse_money3(m["c"]) or 0, parse_money3(m["b"]) or 0
            )
            if prev_row is None:
                row.lead = [t for t, _ in gap]
            else:
                k = _split_gap(gap, len(prev_row.lead))
                prev_row.trail.extend(t for t, _ in gap[:k])
                row.lead = [t for t, _ in gap[k:]]
            gap, prev_row = [], row
            cur.rows.append(row)
            continue
        if zm := _ZERO_LINE.match(ln):
            if zm["text"]:
                gap.append((zm["text"].strip(), True))
            continue
        gap.append((ln, False))
    flush_boundary()

    out: list[ParsedStatement] = []
    for key in order:
        sec = sections[key]
        stmt = ParsedStatement(
            institution="mashreq",
            kind="bank_account",
            currency=sec.currency,
            statement_date=issue,
            opening_minor=sec.opening,
            closing_minor=sec.closing,
            last4=(sec.iban or sec.account)[-4:],
            product=sec.product,
            holder_name=holder,
            warnings=warnings[key],
        )
        if sec.period:
            stmt.period_start, stmt.period_end = sec.period
        prev_bal = sec.opening
        for row in sec.rows:
            if row.debit == 0 and row.credit == 0:
                stmt.warnings.append("skipped zero-amount row")
                continue
            direction = Direction.credit if row.credit > row.debit else Direction.debit
            amount = abs(row.credit - row.debit)
            if prev_bal is not None:
                delta = row.balance - prev_bal
                if delta != (amount if direction == Direction.credit else -amount):
                    stmt.warnings.append("running balance does not match amount")
            prev_bal = row.balance
            stmt.lines.append(
                StatementLineData(
                    posted_on=row.posted_on,
                    description=_description(row),
                    amount_minor=amount,
                    direction=direction,
                    currency=sec.currency,
                    balance_minor=row.balance,
                    reference=row.ref,
                )
            )
        if stmt.period_end is None and stmt.lines:
            stmt.period_start = stmt.period_start or min(ln.posted_on for ln in stmt.lines)
            stmt.period_end = max(ln.posted_on for ln in stmt.lines)
        out.append(stmt)
    return out, issue


class MashreqParser(LayoutStatementParser):
    """Cards via the shared Layout; NEO account PDFs via the section parser above."""

    def parse_sections(
        self, doc: StatementDocument, kind: StatementKind | None = None
    ) -> list[ParsedStatement]:
        kind = kind or "bank_account"
        if kind == "bank_account" and any(
            _TABLE_HEADER.match(re.sub(r"\s+", " ", ln).strip()) for ln in doc.text.splitlines()
        ):
            sections, _ = parse_account_sections(doc.text)
            for s in sections:
                s.parser = f"mashreq:{kind}"
            if any(s.lines for s in sections):
                return sections
        one = super().parse(doc, kind)
        return [one] if one else []

    def parse(self, doc: StatementDocument, kind: StatementKind | None = None) -> ParsedStatement | None:
        sections = self.parse_sections(doc, kind)
        return next((s for s in sections if s.lines), None)


PARSER = MashreqParser("mashreq", card=CARD, account=ACCOUNT)
__all__ = ["ACCOUNT", "CARD", "PARSER", "MashreqParser", "parse_account_sections"]

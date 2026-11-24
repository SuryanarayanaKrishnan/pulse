"""Configurable, line-oriented statement parser (``pdfplumber`` text -> ``ParsedStatement``).

Documented generic layout (what the per-bank modules specialise):

Credit card statement::

    <BANK NAME>  Credit Card Statement
    Card Number: 4111 11XX XXXX 1234
    Statement Period: 26 Feb 2026 to 25 Mar 2026        (or "Statement Date: 25 Mar 2026")
    Payment Due Date: 19 Apr 2026
    Previous Balance        1,000.00
    Payments / Credits        500.00
    Purchases / Charges       800.00
    Total Amount Due        1,300.00        <- closing balance
    Minimum Payment Due       100.00
    Date        Description                    Amount (AED)
    02 Mar 2026 CARREFOUR DUBAI AE                120.50
    05 Mar 2026 PAYMENT RECEIVED - THANK YOU      500.00 Cr
    07 Mar 2026 AMAZON US SEATTLE US  USD 10.00    36.73   <- foreign amount, then AED amount

Bank account statement::

    Account Statement   Account Number: 1234567890123   IBAN: AE07 0331 2345 6789 0123 456
    Statement Period: 01 Mar 2026 to 31 Mar 2026
    Opening Balance 5,000.00        Closing Balance 4,380.00
    Date        Description                       Amount     Balance
    01 Mar 2026 POS PURCHASE CARREFOUR             120.50   4,879.50
    03 Mar 2026 SALARY CREDIT                    1,000.00   5,879.50

Rules: a transaction row starts with a date (optionally followed by a posting date); trailing
money tokens are the amount(s); card rows may carry ``<CCY> <foreign amount>`` before the AED amount;
account rows carry a running balance as the last token, which fixes the direction (balance delta)
even when the statement shows no Dr/Cr markers. Unmarked card rows are debits; ``Cr`` / ``CR`` /
a leading ``-`` mark credits. Rows without a date that look like text continue the previous
description. Everything that does not fit is ignored, and anything suspicious is reported in
``warnings`` so reconciliation can flag it.
"""

import re
from dataclasses import dataclass, field, replace
from datetime import date

from app.models.transaction import Direction
from app.services.parsers.dates import parse_date
from app.services.parsers.statement_base import (
    ParsedStatement,
    StatementDocument,
    StatementKind,
    StatementLineData,
)
from app.services.parsers.statements.money import AMOUNT_RE, parse_money

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
LINE_DATE = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|\d{{1,2}}[/.\-]\d{{1,2}}[/.\-](?:\d{{4}}|\d{{2}})(?!\d)"
    rf"|\d{{1,2}}[ \-]{_MONTH}[ \-,]+(?:\d{{4}}|\d{{2}})(?!\d))"
)
_ROW_START = re.compile(rf"^(?P<d1>{LINE_DATE})(?:\s+(?P<d2>{LINE_DATE}))?\s+(?P<rest>\S.*)$", re.IGNORECASE)
_AMOUNT_TOKEN = re.compile(rf"^(?:{AMOUNT_RE})$")
_KNOWN_CCY = {
    "AED", "USD", "EUR", "GBP", "SAR", "INR", "PKR", "QAR", "KWD", "BHD", "OMR", "EGP", "CHF", "JPY",
    "CAD", "AUD", "SGD", "CNY", "THB", "TRY", "LKR", "PHP", "HKD", "MYR", "IDR", "KRW", "NZD", "ZAR",
}  # fmt: skip
_OPENING_ROW = re.compile(r"^(?:opening balance|balance (?:brought forward|b/?f)|previous balance)\b", re.I)
_CLOSING_ROW = re.compile(r"^(?:closing balance|balance (?:carried forward|c/?f))\b", re.I)
_CREDIT_WORDS = re.compile(
    r"\b(?:payment received|payment\s*-?\s*thank you|thank you for your payment|refund|reversal|cashback|"
    r"salary|credit interest|deposit)\b",
    re.I,
)
_CONTINUATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,&'*/\-#@()]{1,70}$")
_NOISE = re.compile(
    r"\b(?:page\s+\d+|statement|total|balance|limit|minimum|payment due|due date|date|description|amount|"
    r"transaction|summary|reward|points|customer|please|account|iban|swift|vat|trn)\b",
    re.I,
)
_CARD_NUM = re.compile(r"(?:card\s*(?:no\.?|number|#)?|credit card)[:\s]*([\dXx*•\- ]{12,25})", re.I)
_ACCT_NUM = re.compile(r"(?:account\s*(?:no\.?|number|#)|a/c\s*(?:no\.?)?)[:\s]*([\dXx*\-]{6,30})", re.I)
_IBAN = re.compile(r"\bIBAN[:\s]*(AE\d{2}(?:\s?\d{4}){4}\s?\d{3})", re.I)
_CCY_LABEL = re.compile(r"(?:account |statement )?currency[:\s]+([A-Z]{3})\b", re.I)
_PERIOD = re.compile(
    rf"(?:statement\s+period|period|from)[:\s]*(?P<a>{LINE_DATE})\s*(?:to|-|–|through|till)\s*(?P<b>{LINE_DATE})",
    re.I,
)


@dataclass(frozen=True)
class Labels:
    """Regex alternations (no capture groups) for the summary block. First that matches wins."""

    opening: tuple[str, ...] = (
        r"previous (?:statement )?balance",
        r"opening balance",
        r"balance brought forward",
    )
    closing: tuple[str, ...] = (
        r"total amount due",
        r"closing balance",
        r"new balance",
        r"statement balance",
        r"total outstanding(?: balance)?",
        r"balance carried forward",
    )
    min_due: tuple[str, ...] = (r"minimum (?:payment|amount)(?: due)?", r"min\.? (?:payment|amount)(?: due)?")
    due_date: tuple[str, ...] = (r"payment due date", r"due date", r"pay(?:ment)? by")
    statement_date: tuple[str, ...] = (r"statement date", r"statement closing date", r"closing date")
    payments: tuple[str, ...] = (r"payments?(?: (?:and|&|/) credits?)?", r"total credits?")
    purchases: tuple[str, ...] = (
        r"purchases?(?: (?:and|&|/) (?:charges|debits|cash advances?))?",
        r"total debits?",
        r"new charges",
    )


@dataclass(frozen=True)
class Layout:
    labels: Labels = field(default_factory=Labels)
    use_second_date: bool = False  # two-date rows (transaction, posting): which one to use
    foreign_before_local: bool = True  # "USD 10.00 36.73" vs "36.73 USD 10.00"
    has_balance_column: bool = True  # bank accounts: last money token is the running balance
    negative_is_credit: bool = True  # card rows: leading '-' / parentheses mean credit
    infer_credit_from_text: bool = True  # "PAYMENT RECEIVED", "REFUND" without a Cr marker
    summary_table: bool = False  # summary printed as a header row of labels + a row of values
    extra_noise: tuple[str, ...] = ()


def _label_re(alts: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z])(?:{'|'.join(alts)})(?![A-Za-z])", re.I)


def detect_kind(text: str) -> StatementKind:
    if re.search(
        r"credit card statement|card number|minimum (?:payment|amount)|payment due date|credit limit",
        text,
        re.I,
    ):
        return "credit_card"
    return "bank_account"


def _last4(num: str) -> str | None:
    digits = re.sub(r"\D", "", num[-8:])
    return digits[-4:] if len(digits) >= 4 else None


class LayoutStatementParser:
    """Concrete ``StatementParser`` for one institution: a card Layout and an account Layout."""

    def __init__(
        self,
        institution: str,
        *,
        card: Layout | None = None,
        account: Layout | None = None,
        markers: tuple[str, ...] = (),
    ) -> None:
        self.institution = institution
        self.card = card or Layout(has_balance_column=False)
        self.account = account or Layout()
        self._markers = [re.compile(rf"(?<![A-Za-z0-9]){re.escape(m)}(?![A-Za-z0-9])", re.I) for m in markers]

    def matches(self, sender: str | None, text: str) -> bool:
        from app.services.institutions import institutions

        if sender and institutions.from_email_address(sender) == self.institution:
            return True
        return institutions.detect_in_text(text) == self.institution

    def parse(self, doc: StatementDocument, kind: StatementKind | None = None) -> ParsedStatement | None:
        kind = kind or detect_kind(doc.text)
        layout = self.card if kind == "credit_card" else self.account
        stmt = parse_text(doc.text, kind, layout)
        stmt.institution = self.institution
        stmt.parser = f"{self.institution}:{kind}"
        return stmt if stmt.lines else None


class GenericLayoutParser(LayoutStatementParser):
    """Bank-agnostic default layout; used when no institution parser fits."""

    def __init__(self) -> None:
        super().__init__("generic")

    def matches(self, sender: str | None, text: str) -> bool:
        return True

    def parse(self, doc: StatementDocument, kind: StatementKind | None = None) -> ParsedStatement | None:
        stmt = super().parse(doc, kind)
        if stmt is not None:
            stmt.institution = None
        return stmt


def _money_tokens(rest: str, foreign_after: bool = False) -> tuple[str, list[str], str | None]:
    """Split trailing money tokens off a row -> (leading text, tokens in order, foreign currency).

    ``foreign_after`` handles the "<AED amount> USD 10.00" ordering, where the currency code sits
    between the two amounts; the default "USD 10.00 36.73" leaves the code at the end of the text.
    """
    parts = rest.split()
    tokens: list[str] = []
    i = len(parts)
    while i > 0 and len(tokens) < 4:
        tok = parts[i - 1]
        # "250.00 Cr" is two whitespace-separated parts: glue a lone marker onto the amount before it.
        if tok.upper() in ("CR", "DR") and i >= 2 and _AMOUNT_TOKEN.match(parts[i - 2]):
            tokens.append(f"{parts[i - 2]} {tok.upper()}")
            i -= 2
            continue
        if _AMOUNT_TOKEN.match(tok):
            tokens.append(tok)
            i -= 1
            continue
        break
    ccy = None
    if (
        foreign_after
        and len(tokens) == 1
        and i >= 2
        and parts[i - 1] in _KNOWN_CCY
        and _AMOUNT_TOKEN.match(parts[i - 2])
    ):
        ccy = parts[i - 1]
        tokens.append(parts[i - 2])
        i -= 2
    tokens.reverse()
    return " ".join(parts[:i]), tokens, ccy


def _row_to_line(
    d1: date,
    d2: date | None,
    desc: str,
    tokens: list[str],
    ccy_hint: str | None,
    kind: StatementKind,
    layout: Layout,
    prev_balance: int | None,
) -> tuple[StatementLineData | None, str | None]:
    posted = d2 if layout.use_second_date and d2 else d1
    money = [m for t in tokens if (m := parse_money(t)) is not None]
    if not money:
        return None, "row without amount"
    balance = None
    if kind == "bank_account" and layout.has_balance_column and len(money) >= 2:
        bal = money[-1]
        balance = -bal.minor if (bal.marker == "DR" or bal.negative) else bal.minor
        money = money[:-1]
    foreign_amt = foreign_ccy = None
    words = desc.split()
    if len(money) >= 2:
        if layout.foreign_before_local:
            foreign_m, local = money[-2], money[-1]
        else:
            local, foreign_m = money[-2], money[-1]
        money = [local]
        foreign_amt = foreign_m.minor
        foreign_ccy = ccy_hint
        if foreign_ccy is None and words and words[-1] in _KNOWN_CCY:
            foreign_ccy = words.pop()
        desc = " ".join(words)
    amt = money[0]
    if amt.minor == 0:
        return None, "zero amount"

    direction: Direction | None = None
    note = None
    if balance is not None and prev_balance is not None:
        delta = balance - prev_balance
        if abs(delta) == amt.minor:
            direction = Direction.credit if delta > 0 else Direction.debit
        else:
            note = "running balance does not match amount"
    if direction is None:
        if amt.marker == "CR":
            direction = Direction.credit
        elif amt.marker == "DR":
            direction = Direction.debit
        elif kind == "credit_card" and layout.negative_is_credit and amt.negative:
            direction = Direction.credit
        elif kind == "bank_account" and amt.negative:
            direction = Direction.debit
        elif layout.infer_credit_from_text and _CREDIT_WORDS.search(desc):
            direction = Direction.credit
        else:
            direction = Direction.debit
            if kind == "bank_account":
                note = note or "direction assumed debit"
    return (
        StatementLineData(
            posted_on=posted,
            description=desc.strip(),
            amount_minor=amt.minor,
            direction=direction,
            foreign_amount_minor=foreign_amt,
            foreign_currency=foreign_ccy,
            balance_minor=balance,
        ),
        note,
    )


def parse_text(text: str, kind: StatementKind, layout: Layout) -> ParsedStatement:
    stmt = ParsedStatement(institution=None, kind=kind)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    extract_summary(lines, stmt, layout)
    extra_noise = [re.compile(p, re.I) for p in layout.extra_noise]

    prev_balance = stmt.opening_minor if kind == "bank_account" else None
    can_continue = False  # True right after a transaction row: a wrapped description may follow
    for ln in lines:
        m = _ROW_START.match(ln)
        if not m:
            if (
                can_continue
                and _CONTINUATION.match(ln)
                and not _NOISE.search(ln)
                and not any(p.search(ln) for p in extra_noise)
                and not _money_tokens(ln)[1]
                and len(stmt.lines[-1].description) < 90
            ):
                prev = stmt.lines[-1]
                stmt.lines[-1] = replace(prev, description=f"{prev.description} {ln}")
            else:
                can_continue = False
            continue
        can_continue = False
        d1, d2 = parse_date(m["d1"]), (parse_date(m["d2"]) if m["d2"] else None)
        if d1 is None:
            continue
        desc, tokens, ccy_hint = _money_tokens(
            m["rest"], not layout.foreign_before_local and kind == "credit_card"
        )
        if not tokens or any(p.search(ln) for p in extra_noise):
            continue
        if _OPENING_ROW.match(desc) or _CLOSING_ROW.match(desc):
            value = parse_money(tokens[-1])
            if value is not None:
                if kind == "bank_account":
                    signed = -value.minor if (value.marker == "DR" or value.negative) else value.minor
                else:
                    signed = -value.minor if value.marker == "CR" else value.minor
                if _OPENING_ROW.match(desc) and stmt.opening_minor is None:
                    stmt.opening_minor = signed
                    prev_balance = signed if kind == "bank_account" else prev_balance
                elif _CLOSING_ROW.match(desc) and stmt.closing_minor is None:
                    stmt.closing_minor = signed
            continue
        line, note = _row_to_line(d1, d2, desc, tokens, ccy_hint, kind, layout, prev_balance)
        if line is None:
            if note and note != "zero amount":
                stmt.warnings.append(f"skipped row: {ln[:60]} ({note})")
            continue
        if note:
            stmt.warnings.append(f"{note}: {ln[:60]}")
        stmt.lines.append(replace(line, currency=stmt.currency))
        can_continue = True
        if line.balance_minor is not None:
            prev_balance = line.balance_minor
        elif kind == "bank_account":
            prev_balance = None  # lost the running balance; fall back to markers
    finish(stmt)
    return stmt


def _first_money(line: str, after: int) -> int | None:
    """First money token after position ``after`` -> signed minor (Cr/negative => negative on cards)."""
    for m in re.finditer(AMOUNT_RE, line[after:]):
        money = parse_money(m.group(0))
        if money is not None:
            return money.minor if not (money.negative or money.marker == "CR") else -money.minor
    return None


def extract_summary(lines: list[str], stmt: ParsedStatement, layout: Layout) -> None:
    labels = layout.labels
    kind = stmt.kind
    patterns = {
        "opening": _label_re(labels.opening),
        "closing": _label_re(labels.closing),
        "min_due": _label_re(labels.min_due),
        "payments": _label_re(labels.payments),
        "purchases": _label_re(labels.purchases),
    }
    due_re, stmt_date_re = _label_re(labels.due_date), _label_re(labels.statement_date)
    for ln in lines:
        if _ROW_START.match(ln):
            continue  # transaction rows are not summary lines
        if m := _CCY_LABEL.search(ln):
            stmt.currency = m.group(1).upper()
        if stmt.last4 is None:
            if kind == "credit_card" and (cm := _CARD_NUM.search(ln)):
                stmt.last4 = _last4(cm.group(1).strip())
            elif kind == "bank_account":
                if im := _IBAN.search(ln):
                    stmt.last4 = _last4(im.group(1).replace(" ", ""))
                elif am := _ACCT_NUM.search(ln):
                    stmt.last4 = _last4(am.group(1))
        if stmt.period_start is None and (pm := _PERIOD.search(ln)):
            stmt.period_start, stmt.period_end = parse_date(pm["a"]), parse_date(pm["b"])
        due_line = False
        for rx, target in ((due_re, "due_date"), (stmt_date_re, "statement_date")):
            if mm := rx.search(ln):
                due_line = True
                dm = re.search(LINE_DATE, ln[mm.end() :], re.I)
                if dm and getattr(stmt, target) is None and (d := parse_date(dm.group(0))):
                    setattr(stmt, target, d)
        if due_line:
            continue
        # One field per line; "Minimum Payment Due" must not also count as "Payments".
        for name in ("min_due", "opening", "closing", "purchases", "payments"):
            mm = patterns[name].search(ln)
            if not mm:
                continue
            value = _first_money(ln, mm.end())
            if value is None:
                continue
            if name == "opening" and stmt.opening_minor is None:
                stmt.opening_minor = _balance(value, ln, kind)
            elif name == "closing" and stmt.closing_minor is None:
                stmt.closing_minor = _balance(value, ln, kind)
            elif name == "min_due" and stmt.min_due_minor is None:
                stmt.min_due_minor = abs(value)
            elif name == "payments" and stmt.total_payments_minor is None and kind == "credit_card":
                stmt.total_payments_minor = abs(value)
            elif name == "purchases" and stmt.total_purchases_minor is None and kind == "credit_card":
                stmt.total_purchases_minor = abs(value)
            break
    if layout.summary_table:
        _extract_summary_table(lines, stmt, layout)


def _balance(value: int, line: str, kind: StatementKind) -> int:
    """_first_money negates Cr/negative; for bank accounts a DR/negative marker means overdrawn."""
    if kind == "credit_card":
        return value
    return -abs(value) if re.search(r"\bDR\b|-\s?\d", line, re.I) else abs(value)


def _extract_summary_table(lines: list[str], stmt: ParsedStatement, layout: Layout) -> None:
    """Summary printed as a header row of labels followed by a row of values."""
    order = (
        ("opening", layout.labels.opening),
        ("payments", layout.labels.payments),
        ("purchases", layout.labels.purchases),
        ("closing", layout.labels.closing),
        ("min_due", layout.labels.min_due),
    )
    for i, ln in enumerate(lines[:-1]):
        hits = sorted((m.start(), name) for name, alts in order if (m := _label_re(alts).search(ln)))
        if len(hits) < 3:
            continue
        values = [mny for t in re.findall(AMOUNT_RE, lines[i + 1]) if (mny := parse_money(t)) is not None]
        if len(values) != len(hits):
            continue
        for (_, name), money in zip(hits, values, strict=True):
            signed = -money.minor if money.marker == "CR" else money.minor
            if name == "opening" and stmt.opening_minor is None:
                stmt.opening_minor = signed
            elif name == "closing" and stmt.closing_minor is None:
                stmt.closing_minor = signed
            elif name == "min_due" and stmt.min_due_minor is None:
                stmt.min_due_minor = money.minor
            elif name == "payments" and stmt.total_payments_minor is None:
                stmt.total_payments_minor = money.minor
            elif name == "purchases" and stmt.total_purchases_minor is None:
                stmt.total_purchases_minor = money.minor
        return


def finish(stmt: ParsedStatement) -> None:
    dates = [ln.posted_on for ln in stmt.lines]
    if stmt.period_end is None:
        stmt.period_end = stmt.statement_date or (max(dates) if dates else None)
    if stmt.period_start is None and dates:
        stmt.period_start = min(dates)
    if stmt.period_start and stmt.period_end and stmt.period_start > stmt.period_end:
        stmt.period_start, stmt.period_end = stmt.period_end, stmt.period_start
    if dates and stmt.period_end and max(dates) > stmt.period_end:
        stmt.warnings.append("transactions dated after the statement period end")

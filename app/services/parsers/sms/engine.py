"""Template-driven SMS parser used by the per-bank modules in this package.

A bank module declares its sender IDs (via ``app.services.institutions``) and a list of templates.
A template is a regex written with placeholders:

    {AMT}   currency + amount          -> groups cur, amt   ("AED 45.00", "Dhs. 1,200")
    {L4}    masked card/account number -> group  last4      ("XXX1234", "ending 1234", "**1234")
    {M}     merchant / counterparty    -> group  merchant   (lazy; stops at . , ; on/using/ref/avl/bal)
    {DATE}  optional date in the text  -> group  date

Templates are tried in order, bank-specific first, then the shared ``COMMON`` ones. To support a new
SMS wording, add one ``T(...)`` line to the bank's module and a sample to the tests.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models.transaction import Direction
from app.services.institutions import institutions, norm_name
from app.services.parsers.base import ParsedTransaction
from app.services.parsers.dates import DATE_RE, at_date, parse_date

_CURRENCIES = "AED|USD|EUR|GBP|SAR|INR|PKR|QAR|KWD|BHD|OMR|EGP|CHF|JPY|CAD|AUD|SGD|CNY|THB|TRY|LKR|PHP"
_PLACEHOLDERS = {
    "{AMT}": rf"(?P<cur>{_CURRENCIES}|Dhs?\.?)\s?(?P<amt>\d[\d,]*(?:\.\d{{1,3}})?)",
    "{L4}": r"(?:(?:x|X|\*|•){1,}\s?)?(?P<last4>\d{4})\b",
    "{M}": (
        r"(?P<merchant>[A-Za-z0-9&'.\-*/+@ ]{2,60}?)"
        r"(?=\s*(?:\.(?:\s|$)|[,;]|\s(?:on|using|via|with|ref|avl|avail|available|bal|balance|total|date|at|from|"
        r"for|is|was|has|call|if)\b|\s\d{1,2}[/\-]|$))"
    ),
    "{DATE}": rf"(?P<date>{DATE_RE})",
}

_OTP = re.compile(
    r"\b(otp|one[- ]?time (?:password|pin|code)|verification code|do not share|never share|cvv)\b", re.I
)
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Template:
    pattern: re.Pattern[str]
    direction: Direction | None  # None => infer from keywords in the match


def T(template: str, direction: Direction | None = Direction.debit) -> Template:  # noqa: N802
    expanded = template
    for key, value in _PLACEHOLDERS.items():
        expanded = expanded.replace(key, value)
    return Template(re.compile(expanded, re.IGNORECASE), direction)


DR, CR = Direction.debit, Direction.credit

# Wordings shared by most UAE banks. Bank modules put their own phrasing first.
COMMON: tuple[Template, ...] = (
    # "Purchase of AED 45.00 with Credit Card ending 1234 at CARREFOUR ..."
    T(
        r"purchase of {AMT} (?:with|on|using) (?:your )?(?:[\w ]{0,25}?)(?:credit |debit |cr\. ?|dr\. ?)?card[^.]{0,12}?{L4}[, ]+at {M}"
    ),
    # "Your Cr. Card XXX1234 was used for AED 45.00 at CARREFOUR ..."
    T(
        r"(?:credit |debit |cr\. ?|dr\. ?)?card[^.]{0,20}?{L4} (?:was|has been) (?:used|charged|debited)(?: for| with)? {AMT}(?: at {M})?"
    ),
    # "AED 45.00 spent on your card ending 1234 at CARREFOUR"
    T(
        r"{AMT} (?:has been |was )?(?:spent|charged|debited|used)(?: on| using| with| from)? (?:your )?[\w ]{0,25}?card[^.]{0,12}?{L4}(?:[, ]+at {M})?"
    ),
    # "Spent AED 45.00 at CARREFOUR on card ending 1234" / "Purchase of AED 45 at X on card ending 1234"
    T(
        r"(?:spent|purchase of|paid|payment of) {AMT} at {M}(?: on| using| with)?(?: your)? [\w ]{0,20}?card[^.]{0,12}?{L4}"
    ),
    # ATM
    T(r"(?:atm )?(?:cash )?withdrawal of {AMT}[^.]{0,40}?{L4}(?:[^.]{0,20}? at {M})?"),
    # Account debits: "Your a/c XXXX5678 has been debited with AED 45.00 ..."
    T(
        r"(?:a/c|acct|account)[^.]{0,12}?{L4} (?:has been |was )?debited (?:with|by|for)? ?{AMT}(?: (?:for|to|at) {M})?"
    ),
    # Account credits
    T(
        r"{AMT} (?:has been |was )?credited to (?:your )?(?:a/c|acct|account|card)[^.]{0,12}?{L4}(?:[^.]{0,20}? (?:from|by) {M})?",
        CR,
    ),
    T(
        r"(?:a/c|acct|account)[^.]{0,12}?{L4} (?:has been |was )?credited (?:with|by)? ?{AMT}(?: (?:from|by) {M})?",
        CR,
    ),
    T(r"(?:salary|refund) (?:of )?{AMT}[^.]{0,40}?{L4}", CR),
    # Card payment received
    T(r"payment of {AMT} (?:has been )?(?:received|credited)[^.]{0,40}?{L4}", CR),
)


class SmsParser:
    """Concrete Parser for one institution, driven by templates."""

    def __init__(
        self, institution: str, templates: Sequence[Template], *, name_hints: Sequence[str] = ()
    ) -> None:
        self.institution = institution
        self._templates = (*templates, *COMMON)
        inst = institutions.get(institution)
        self._senders = {norm_name(s) for s in (inst.sms_senders if inst else ())}
        self._hints = [
            re.compile(rf"(?<![A-Za-z0-9]){re.escape(h)}(?![A-Za-z0-9])", re.I) for h in name_hints
        ]

    def matches(self, sender: str | None, text: str) -> bool:
        if sender and norm_name(sender) in self._senders:
            return True
        return any(h.search(text) for h in self._hints)

    def parse(self, text: str, *, received_at: datetime | None = None) -> ParsedTransaction | None:
        text = _WS.sub(" ", text).strip()
        if not text or _OTP.search(text):
            return None
        for tpl in self._templates:
            m = tpl.pattern.search(text)
            if not m:
                continue
            groups = m.groupdict()
            direction = tpl.direction or (
                CR if re.search(r"credit|refund|deposit|salary|received", m[0], re.I) else DR
            )
            cur = groups["cur"].upper()
            currency = "AED" if cur.startswith("DH") else cur
            amount_minor = int((Decimal(groups["amt"].replace(",", "")) * 100).to_integral_value())
            if amount_minor <= 0:
                continue
            posted_at = received_at
            date_s = groups.get("date") or self._find_date(text)
            if date_s and (d := parse_date(date_s)) is not None:
                posted_at = at_date(d, received_at)
            merchant = (groups.get("merchant") or "").strip(" .-,") or None
            return ParsedTransaction(
                amount_minor=amount_minor,
                currency=currency,
                direction=direction,
                merchant_raw=merchant,
                last4=groups.get("last4"),
                posted_at=posted_at,
                institution=self.institution,
                confidence=0.85,
            )
        return None

    @staticmethod
    def _find_date(text: str) -> str | None:
        m = re.search(rf"\bon\s+({DATE_RE})", text, re.I)
        return m.group(1) if m else None

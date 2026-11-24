"""Person-to-person transfer detection on statement lines (feeds on-device split auto-settlement).

``extract_counterparty(description, direction)`` returns who the money went to / came from when the
description is a transfer to an individual (IBAN / Aani-IPP / mobile number / remittance), else ``None``.
Own-account moves ("Own Account Transfer", "Account to Account", card payments, or a counterparty whose
name matches the account holder) are not person-to-person and return ``None``.

Rules (kept deliberately small so the Dart port can mirror them one-to-one):

1. Skip when the text says it is an own/card move: ``credit card payment``, ``card payment``,
   ``own account``, ``account to account``, ``cardless cash``, or the account part is a masked card number.
2. IPP/Aani/instant transfer: ``<12 digit prefix?><IBAN> - <NAME> - /REF/ <purpose>``; the name sits
   between the account token and ``/REF/``.
3. QuickRemit / local fund transfer: ``... to A/C <acct> - <NAME> With Transaction Ref#`` or
   ``Local Fund Transfer, <NAME>, A/C No: <acct>``.
4. Inward remittance: the line after ``Inward Remittance`` is the sender, the following one the bank.
5. Mobile-number transfers (Aani by number): a UAE mobile anywhere in the text gives ``phone`` (E.164),
   the name is the words after ``to``/``from``/``beneficiary``/``sender`` (or before the number).
6. Own filter: every name token is a prefix match of a holder-name token (statements truncate names).
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.models.transaction import Direction
from app.services.phone import InvalidPhoneError, normalise_phone

Side = Literal["outgoing", "incoming"]


@dataclass(frozen=True, slots=True)
class Counterparty:
    name: str
    direction: Side
    phone: str | None = None  # E.164
    iban_tail: str | None = None  # last 4 digits of the beneficiary IBAN / account number


_OWN_MOVE = re.compile(
    r"credit\s+card\s+payment|card\s+payment|own\s+account|account\s+to\s+account|cardless\s+cash|"
    r"\bATM\b|salary",
    re.I,
)
_MASKED_CARD = re.compile(r"\d{4,}X{4,}\d{4}", re.I)
_IBAN = re.compile(r"(?:\d{12})?(AE\d{2}\d{19})(?!\d)")
_IPP = re.compile(r"\b(?:IPP|AANI|INSTANT)\b", re.I)
_IPP_NAME = re.compile(r"(?:AE\d{21}|\d{4,}X{4,}\d{4}|\d{8,})\s*-\s*(?P<name>[^/]+?)\s*-\s*/REF/", re.I)
_QUICKREMIT = re.compile(
    r"QuickRemit\s+\d+\s+to\s+A/C\s+(?P<acct>\d+)\s*-\s*(?P<name>.+?)\s+With\s+Transaction", re.I
)
_LOCAL = re.compile(r"Local\s+Fund\s+Transfer,?\s*(?P<name>[^,]+?),\s*A/C\s*No:?\s*(?P<acct>\d+)", re.I)
_REMIT = re.compile(r"Inward\s+Remittance\s+(?P<rest>.+)$", re.I)
_BANK_WORD = re.compile(r"^(?:BANK|LIMITED|LTD|PJSC|PLC|LLC|BRANCH|N\.A\.|EXCHANGE|FINANCE)$", re.I)
_PHONE = re.compile(r"(?<![\d+])(?:\+|00)?(?:971[\s-]?|0)5[024568][\s-]?\d{3}[\s-]?\d{4}(?!\d)")
_NAME_AFTER = re.compile(
    r"\b(?:to|from|beneficiary|sender|ben|by)\b[:\s]+(?P<name>[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){0,3})",
    re.I,
)
_STOP_WORDS = {"aed", "ref", "transfer", "payment", "mobile", "number", "via", "aani", "ipp", "no", "with"}


def _clean_name(raw: str) -> str | None:
    words = [w for w in re.split(r"\s+", raw.strip(" -,.")) if w]
    kept: list[str] = []
    for w in words:
        if w.lower().strip(".") in _STOP_WORDS or re.search(r"\d", w):
            break
        kept.append(w)
    name = " ".join(kept).strip()
    return name if len(name) >= 2 else None


def _remittance_sender(rest: str) -> str | None:
    """ "<SENDER NAME> <BANK NAME ...>": the bank starts one word before its first bank keyword."""
    words = rest.split()
    for i, w in enumerate(words):
        if _BANK_WORD.match(w):
            return _clean_name(" ".join(words[: max(i - 1, 0)]))
    return None


def _tail(acct: str) -> str | None:
    digits = re.sub(r"\D", "", acct)
    return digits[-4:] if len(digits) >= 4 else None


def is_holder(name: str, holder_name: str | None) -> bool:
    """True when ``name`` is (a possibly truncated form of) the statement holder's own name."""
    if not holder_name:
        return False
    holder = [t for t in re.split(r"\s+", holder_name.lower()) if t]
    tokens = [t for t in re.split(r"\s+", name.lower()) if t]
    if not holder or not tokens:
        return False
    return all(any(h.startswith(t) or t.startswith(h) for h in holder) for t in tokens if len(t) >= 3)


def extract_counterparty(
    description: str, direction: Direction | str, holder_name: str | None = None
) -> Counterparty | None:
    text = re.sub(r"\s+", " ", description).strip()
    if not text:
        return None
    side: Side = "incoming" if str(getattr(direction, "value", direction)) == "credit" else "outgoing"
    phone: str | None = None
    if pm := _PHONE.search(text):
        try:
            phone = normalise_phone(pm.group(0))
        except InvalidPhoneError:
            phone = None

    name: str | None = None
    tail: str | None = None
    if _OWN_MOVE.search(text) or _MASKED_CARD.search(text):
        return None
    if (m := _QUICKREMIT.search(text)) or (m := _LOCAL.search(text)):
        name, tail = _clean_name(m["name"]), _tail(m["acct"])
    elif _IPP.search(text) or _IBAN.search(text):
        if (m := _IPP_NAME.search(text)) is not None:
            name = _clean_name(m["name"])
        if im := _IBAN.search(text):
            tail = _tail(im.group(1))
        if name is None and (nm := _NAME_AFTER.search(text)):
            name = _clean_name(nm["name"])
    elif m := _REMIT.search(text):
        name = _remittance_sender(m["rest"])
    elif phone:
        if nm := _NAME_AFTER.search(text):
            name = _clean_name(nm["name"])
        if name is None:
            before = text[: text.find(pm.group(0))] if pm else ""
            tokens = re.findall(r"[A-Za-z][A-Za-z.'-]*", before)
            name = _clean_name(" ".join(tokens[-3:])) if tokens else None
    if name is None or (phone is None and tail is None and not _REMIT.search(text)):
        return None
    if is_holder(name, holder_name):
        return None
    return Counterparty(name=name, direction=side, phone=phone, iban_tail=tail)

"""Fallback parser: shared bank-agnostic templates first, then loose keyword/regex heuristics."""

import re
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.models.transaction import Direction
from app.services.parsers.base import ParsedTransaction
from app.services.parsers.sms.engine import SmsParser

_COMMON = SmsParser("generic", [])

_AMOUNT = re.compile(
    r"(?P<cur>AED|USD|EUR|GBP|Dhs?\.?)\s?(?P<amt>\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_LAST4 = re.compile(r"(?:card|a/c|acct|account)[^\d]{0,20}(?:x+|\*+)?(\d{4})\b", re.IGNORECASE)
_MERCHANT = re.compile(
    r"\b(?:at|to|from)\s+(?!your\b)(?P<m>[A-Za-z0-9&'.\-* ]{2,40}?)"
    r"(?=\s+(?:on|using|via|with|ref|avl|available)\b|[.,;]|$)",
    re.IGNORECASE,
)
_DEBIT = re.compile(r"\b(purchase|spent|debited|withdrawn|paid|payment of|charged)\b", re.IGNORECASE)
_CREDIT = re.compile(r"\b(credited|received|deposit(?:ed)?|refund(?:ed)?|salary)\b", re.IGNORECASE)
_OTP = re.compile(r"\b(otp|one[- ]time|verification code)\b", re.IGNORECASE)


class GenericParser:
    institution = "generic"

    def matches(self, sender: str | None, text: str) -> bool:
        return True

    def parse(self, text: str, *, received_at: datetime | None = None) -> ParsedTransaction | None:
        if _OTP.search(text):
            return None
        if (common := _COMMON.parse(text, received_at=received_at)) is not None:
            return replace(common, institution=None, confidence=0.6)
        amount_m = _AMOUNT.search(text)
        if not amount_m:
            return None
        credit, debit = _CREDIT.search(text), _DEBIT.search(text)
        if not credit and not debit:
            return None
        # Whichever keyword appears first wins ("Purchase ... refund policy" -> debit).
        direction = (
            Direction.credit if credit and (not debit or credit.start() < debit.start()) else Direction.debit
        )

        currency = amount_m.group("cur").upper()
        currency = "AED" if currency.startswith("DH") else currency
        amount_minor = int(Decimal(amount_m.group("amt").replace(",", "").rstrip(".")) * 100)
        merchant_m = _MERCHANT.search(text)
        last4_m = _LAST4.search(text)
        return ParsedTransaction(
            amount_minor=amount_minor,
            currency=currency,
            direction=direction,
            merchant_raw=merchant_m.group("m").strip() if merchant_m else None,
            last4=last4_m.group(1) if last4_m else None,
            posted_at=received_at,
            institution=None,
            confidence=0.4,
        )

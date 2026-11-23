"""Parser interface for bank messages. Real UAE bank parsers plug in here later."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.transaction import Direction


@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    amount_minor: int
    currency: str
    direction: Direction
    merchant_raw: str | None = None
    last4: str | None = None
    posted_at: datetime | None = None
    institution: str | None = None
    confidence: float = 0.5


@runtime_checkable
class Parser(Protocol):
    """One implementation per institution (e.g. ENBD, ADCB, FAB, Mashreq)."""

    institution: str

    def matches(self, sender: str | None, text: str) -> bool:
        """Cheap check: does this message look like it came from this institution?"""
        ...

    def parse(self, text: str, *, received_at: datetime | None = None) -> ParsedTransaction | None:
        """Return a transaction, or None when the message is not a transaction (OTP, promo...)."""
        ...

import re
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.statement import ParseStatus
from app.models.transaction import Direction
from app.schemas.common import Currency, PositiveMinor


class StructuredFields(BaseModel):
    """Optional pre-parsed fields (e.g. an Apple Shortcut that already extracted them)."""

    amount_minor: PositiveMinor
    currency: Currency = "AED"
    direction: Direction = Direction.debit
    merchant: str | None = Field(default=None, max_length=500)
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    posted_at: datetime | None = None
    card_name: str | None = Field(default=None, max_length=120, description="Used when last4 is not known")


class IngestRequest(BaseModel):
    raw_text: str | None = Field(default=None, max_length=5000)
    sender: str | None = Field(default=None, max_length=80)
    institution: str | None = Field(default=None, max_length=80)
    account_id: UUID | None = None
    received_at: datetime | None = None
    external_id: str | None = Field(default=None, max_length=128, description="Client-side unique id")
    app_name: str | None = Field(default=None, max_length=120, description="Notification source app")
    title: str | None = Field(default=None, max_length=300)
    fields: StructuredFields | None = None

    @model_validator(mode="after")
    def _need_content(self) -> "IngestRequest":
        if not (self.raw_text and self.raw_text.strip()) and self.fields is None:
            raise ValueError("Provide raw_text or fields")
        return self


class IngestOut(BaseModel):
    id: UUID
    duplicate: bool
    parse_status: ParseStatus


_CCY = re.compile(r"\b([A-Z]{3})\b")


class WalletTap(BaseModel):
    """What an iOS Shortcuts "Transaction" automation / Google Wallet / Samsung Wallet relay can post.

    `amount` may be a number or the text Shortcuts hands over ("AED 45.50", "1,234.50").
    """

    amount: Any
    merchant: str | None = Field(default=None, max_length=500)
    card: str | None = Field(default=None, max_length=120, description="Card name as the wallet shows it")
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    timestamp: datetime | None = None
    currency: Currency | None = None
    direction: Direction = Direction.debit
    external_id: str | None = Field(default=None, max_length=128)
    institution: str | None = Field(default=None, max_length=80)

    @model_validator(mode="before")
    @classmethod
    def _amount(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "amount" not in data:
            return data
        raw = data["amount"]
        data = dict(data)
        if isinstance(raw, str):
            m = _CCY.search(raw.upper())
            if m and not data.get("currency"):
                data["currency"] = m.group(1)
            cleaned = re.sub(r"[^0-9.,\-]", "", raw)
            if "," in cleaned and "." not in cleaned and re.search(r",\d{1,2}$", cleaned):
                cleaned = cleaned.replace(",", ".")  # "45,50"
            raw = cleaned.replace(",", "")
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("amount is not a number") from exc
        if value < 0:  # some wallets report refunds as negative
            value = -value
            data.setdefault("direction", "credit")
        minor = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        if minor <= 0 or minor > 10**15:
            raise ValueError("amount must be positive")
        data["amount"] = minor
        return data

    def to_request(self) -> "IngestRequest":
        return IngestRequest(
            fields=StructuredFields(
                amount_minor=self.amount,
                currency=self.currency or "AED",
                direction=self.direction,
                merchant=self.merchant,
                last4=self.last4,
                posted_at=self.timestamp,
                card_name=self.card,
            ),
            institution=self.institution,
            received_at=self.timestamp,
            external_id=self.external_id,
        )


class WalletIngestOut(IngestOut):
    transaction_id: UUID | None = None
    merged_with_existing: bool = Field(
        default=False, description="True when this tap was the same purchase as an SMS/earlier capture"
    )
    nudge: dict[str, Any] | None = Field(default=None, description="Rewards nudge, when one applies")

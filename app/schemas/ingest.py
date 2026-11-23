from datetime import datetime
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

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.transaction import Direction, TxnSource
from app.schemas.common import Currency, PositiveMinor, Timestamps


class TransactionCreate(BaseModel):
    account_id: UUID
    amount_minor: PositiveMinor
    currency: Currency | None = None  # defaults to the account currency
    direction: Direction
    merchant_raw: str | None = Field(default=None, max_length=500)
    merchant_normalized: str | None = Field(default=None, max_length=160)
    category_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)
    posted_at: datetime
    external_hash: str | None = Field(default=None, min_length=8, max_length=64)


class TransactionUpdate(BaseModel):
    category_id: UUID | None = None
    merchant_normalized: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)
    # When the category is set, remember it as "always categorise this merchant as X".
    remember_merchant: bool = True
    apply_to_existing: bool = Field(
        default=False, description="Also re-categorise past transactions of this merchant"
    )


class TransactionOut(Timestamps):
    user_id: UUID
    account_id: UUID
    statement_id: UUID | None
    category_id: UUID | None
    amount_minor: int
    currency: str
    direction: Direction
    merchant_raw: str | None
    merchant_normalized: str | None
    notes: str | None
    posted_at: datetime
    source: TxnSource

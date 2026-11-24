from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Currency, MinorAmount, Timestamps

DocKind = Literal[
    "title_deed", "fd_certificate", "insurance", "passport", "emirates_id", "visa",
    "loan_agreement", "tenancy", "will", "warranty", "other",
]  # fmt: skip
ReminderKind = Literal["maturity", "renewal", "expiry", "custom"]


class ReminderIn(BaseModel):
    kind: ReminderKind
    due_on: date
    label: str | None = Field(default=None, max_length=160)
    remind_days_before: int = Field(default=30, ge=0, le=365)


class ReminderOut(Timestamps):
    document_id: UUID
    kind: str
    label: str | None
    due_on: date
    remind_days_before: int
    done_at: datetime | None
    days_left: int


class DocumentCreate(BaseModel):
    kind: DocKind
    title: str = Field(min_length=1, max_length=200)
    institution: str | None = Field(default=None, max_length=160)
    reference: str | None = Field(default=None, max_length=120)
    amount_minor: MinorAmount | None = None
    currency: Currency = "AED"
    asset_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)
    reminders: list[ReminderIn] = Field(default_factory=list, max_length=20)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    institution: str | None = Field(default=None, max_length=160)
    reference: str | None = Field(default=None, max_length=120)
    amount_minor: MinorAmount | None = None
    notes: str | None = Field(default=None, max_length=4000)


class DocBackupIn(BaseModel):
    storage_key: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    encrypted: bool


class DocumentOut(Timestamps):
    kind: str
    title: str
    institution: str | None
    reference: str | None
    amount_minor: int | None
    currency: str
    asset_id: UUID | None
    notes: str | None
    has_backup: bool
    backup_size: int | None
    reminders: list[ReminderOut]
    next_due_on: date | None


class DueItemOut(BaseModel):
    source: Literal["document", "asset"]
    id: UUID
    title: str
    kind: str
    due_on: date
    days_left: int
    status: Literal["overdue", "due_soon", "upcoming"]
    reminder_id: UUID | None = None


class RemindersOut(BaseModel):
    as_of: date
    next_due: DueItemOut | None
    items: list[DueItemOut]

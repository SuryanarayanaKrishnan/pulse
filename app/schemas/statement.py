from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from app.models.statement import ParseStatus, ReconciliationStatus
from app.schemas.common import Timestamps
from app.schemas.transaction import TransactionOut


class StatementOut(Timestamps):
    """Never exposes the stored file key."""

    account_id: UUID | None
    institution: str | None
    kind: str | None
    source: str
    period_start: date | None
    period_end: date | None
    currency: str
    opening_balance_minor: int | None
    closing_balance_minor: int | None
    parse_status: ParseStatus
    parse_error: str | None
    reconciliation_status: ReconciliationStatus
    diff_minor: int | None
    original_filename: str | None
    file_sha256: str | None
    summary: dict[str, Any] | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def action_required(self) -> str | None:
        """What the user must do for this statement to progress (surfaced in the UI)."""
        return {
            ParseStatus.needs_password: "set_statement_password",
            ParseStatus.needs_review: "choose_account",
        }.get(self.parse_status)


class StatementUploadOut(BaseModel):
    id: UUID
    duplicate: bool
    parse_status: ParseStatus


class ReprocessRequest(BaseModel):
    account_id: UUID | None = Field(
        default=None, description="Assign/override the account before reprocessing"
    )


class StatementPasswordIn(BaseModel):
    institution: str = Field(min_length=2, max_length=80, description="Institution key or name, e.g. 'enbd'")
    password: str = Field(min_length=1, max_length=256, repr=False)


class StatementPasswordOut(BaseModel):
    """The password is write-only: it is never returned."""

    institution: str
    institution_name: str
    updated_at: datetime


class ReconLine(BaseModel):
    line_no: int
    posted_on: date
    description: str
    amount_minor: int
    currency: str
    direction: str
    foreign_amount_minor: int | None
    foreign_currency: str | None
    transaction_id: UUID | None
    match_status: str
    match_score: int | None


class ReconBalances(BaseModel):
    opening_minor: int | None
    closing_minor: int | None
    computed_closing_minor: int | None
    diff_minor: int | None
    total_debits_minor: int
    total_credits_minor: int


class ReconCounts(BaseModel):
    lines: int
    linked: int  # lines matched to an already-captured transaction
    statement_only: int  # lines with no captured counterpart (created from the statement)
    captured_only: int  # captured transactions in the period that the statement does not show


class LinkedPair(BaseModel):
    line: ReconLine
    transaction: TransactionOut


class ReconciliationReport(BaseModel):
    statement_id: UUID
    parse_status: ParseStatus
    reconciliation_status: ReconciliationStatus
    kind: str | None
    currency: str
    account_id: UUID | None
    period_start: date | None
    period_end: date | None
    balances: ReconBalances
    counts: ReconCounts
    linked: list[LinkedPair]
    statement_only: list[ReconLine]
    captured_only: list[TransactionOut]
    warnings: list[str]

import enum
import uuid
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import (
    JSONDict,
    JSONType,
    SoftDeleteMixin,
    TimestampMixin,
    currency_col,
    pk_uuid,
    str_enum,
)


class ParseStatus(enum.StrEnum):
    pending = "pending"
    parsed = "parsed"
    failed = "failed"
    needs_review = "needs_review"
    skipped = "skipped"
    needs_password = "needs_password"  # noqa: S105  encrypted PDF and no (valid) password stored for the institution


class ReconciliationStatus(enum.StrEnum):
    pending = "pending"
    matched = "matched"
    mismatch = "mismatch"


class Statement(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "statements"
    __table_args__ = (
        Index("ix_statements_user_id_period_end", "user_id", "period_end"),
        # Same bytes (or same Gmail attachment) never create two live statements.
        Index(
            "uq_statements_user_id_file_sha256",
            "user_id",
            "file_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND file_sha256 IS NOT NULL"),
        ),
        # Soft-deleted statements keep their external_id so a Gmail re-sync does not resurrect them.
        Index("uq_statements_user_id_external_id", "user_id", "external_id", unique=True),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    # account / period are unknown until the statement has been parsed
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE")
    )
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = currency_col()
    opening_balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    closing_balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    file_storage_key: Mapped[str | None] = mapped_column(String(512))
    parse_status: Mapped[ParseStatus] = mapped_column(
        str_enum(ParseStatus, "parse_status"), server_default="pending"
    )
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        str_enum(ReconciliationStatus, "reconciliation_status"), server_default="pending"
    )
    # statement closing - (opening + parsed transactions); 0 when reconciled
    diff_minor: Mapped[int | None] = mapped_column(BigInteger)

    institution: Mapped[str | None] = mapped_column(String(40))  # canonical key, e.g. "enbd"
    kind: Mapped[str | None] = mapped_column(String(16))  # credit_card | bank_account
    source: Mapped[str] = mapped_column(String(16), server_default="upload")  # upload | gmail
    email_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_connections.id", ondelete="SET NULL")
    )
    # "gmail:<message id>:<attachment index>" for emailed statements
    external_id: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    parse_error: Mapped[str | None] = mapped_column(Text)
    # min due, due date, statement date, payments/purchases totals, last4, warnings...
    summary: Mapped[JSONDict | None] = mapped_column(JSONType)


class StatementLine(Base, TimestampMixin):
    """One row of a parsed statement. Kept so reconciliation can be re-run and reported."""

    __tablename__ = "statement_lines"
    __table_args__ = (
        UniqueConstraint("statement_id", "line_no", name="uq_statement_lines_statement_id_line_no"),
        Index("ix_statement_lines_transaction_id", "transaction_id"),
    )

    id: Mapped[uuid.UUID] = pk_uuid()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE")
    )
    line_no: Mapped[int] = mapped_column(SmallInteger)
    posted_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    amount_minor: Mapped[int] = mapped_column(BigInteger)  # positive, in `currency`
    currency: Mapped[str] = currency_col()
    direction: Mapped[str] = mapped_column(String(8))  # debit | credit
    foreign_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    foreign_currency: Mapped[str | None] = mapped_column(String(3))
    balance_minor: Mapped[int | None] = mapped_column(BigInteger)  # running balance when the layout has one
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL")
    )
    match_status: Mapped[str] = mapped_column(
        String(16), server_default="unmatched"
    )  # created|linked|unmatched
    match_score: Mapped[int | None] = mapped_column(SmallInteger)

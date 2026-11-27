"""Statement parser interface (PDF/CSV bank & card statements).

Mirrors the SMS ``Parser`` protocol (``institution`` / ``matches`` / ``parse``) but takes a whole
document, since a statement is many lines plus a summary block.

Sign convention for balances (documented because it drives reconciliation):
  * credit_card:  balances are amounts OWED; positive = you owe the bank; a "Cr" balance is negative.
                  closing = opening + debits - credits
  * bank_account: balances are funds held; positive = in credit; an overdrawn "Dr" balance is negative.
                  closing = opening + credits - debits
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol, runtime_checkable

from app.models.transaction import Direction
from app.services.parsers.counterparty import Counterparty

StatementKind = Literal["credit_card", "bank_account"]
Table = list[list[str | None]]


@dataclass(frozen=True, slots=True)
class StatementLineData:
    posted_on: date
    description: str
    amount_minor: int  # always positive, in `currency`
    direction: Direction
    currency: str = "AED"
    foreign_amount_minor: int | None = None
    foreign_currency: str | None = None
    balance_minor: int | None = None  # running balance after the line, when the layout has one
    reference: str | None = None  # bank's transaction reference column, when the layout has one
    counterparty: Counterparty | None = None  # person-to-person transfers only


@dataclass(slots=True)
class ParsedStatement:
    institution: str | None
    kind: StatementKind
    currency: str = "AED"
    period_start: date | None = None
    period_end: date | None = None
    statement_date: date | None = None
    opening_minor: int | None = None
    closing_minor: int | None = None
    min_due_minor: int | None = None
    due_date: date | None = None
    total_payments_minor: int | None = None  # card summary: payments/credits for the period
    total_purchases_minor: int | None = None  # card summary: purchases/charges for the period
    last4: str | None = None
    credit_limit_minor: int | None = None  # credit card: the limit the statement prints
    product: str | None = None  # product name printed in the account summary (e.g. a savings plan)
    holder_name: str | None = None  # never persisted; only used to tell own-account moves from transfers
    lines: list[StatementLineData] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    parser: str = ""


@dataclass(frozen=True, slots=True)
class StatementDocument:
    """Everything the extractor got out of a file."""

    text: str  # all pages, newline separated, layout-preserving
    tables: list[Table] = field(default_factory=list)  # pdfplumber tables / CSV rows as one table
    pages: int = 1
    is_csv: bool = False


@runtime_checkable
class StatementParser(Protocol):
    institution: str

    def matches(self, sender: str | None, text: str) -> bool:
        """Cheap check: does this document look like it came from this institution?"""
        ...

    def parse(self, doc: StatementDocument, kind: StatementKind | None = None) -> ParsedStatement | None:
        """Return the parsed statement, or None when this parser cannot read the document."""
        ...

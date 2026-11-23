"""Build the reconciliation report for a processed statement."""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.statement import Statement, StatementLine
from app.models.transaction import Transaction, TxnSource
from app.schemas.statement import (
    LinkedPair,
    ReconBalances,
    ReconciliationReport,
    ReconCounts,
    ReconLine,
)
from app.schemas.transaction import TransactionOut
from app.services.parsers.dates import DUBAI
from app.services.statements.reconcile import compute_reconciliation


async def build_report(session: AsyncSession, stmt: Statement) -> ReconciliationReport:
    lines = list(
        (
            await session.execute(
                select(StatementLine)
                .where(StatementLine.statement_id == stmt.id)
                .order_by(StatementLine.line_no)
            )
        ).scalars()
    )
    txn_ids = [ln.transaction_id for ln in lines if ln.transaction_id]
    txns: dict[object, Transaction] = {}
    if txn_ids:
        rows = await session.execute(select(Transaction).where(Transaction.id.in_(txn_ids)))
        txns = {t.id: t for t in rows.scalars()}

    linked = [
        LinkedPair(
            line=ReconLine.model_validate(ln, from_attributes=True),
            transaction=TransactionOut.model_validate(txns[ln.transaction_id]),
        )
        for ln in lines
        if ln.match_status == "linked" and ln.transaction_id in txns
    ]
    statement_only = [
        ReconLine.model_validate(ln, from_attributes=True) for ln in lines if ln.match_status != "linked"
    ]

    captured_only: list[Transaction] = []
    if stmt.account_id and stmt.period_start and stmt.period_end:
        lo = _start_of_day(stmt.period_start)
        hi = _start_of_day(stmt.period_end) + timedelta(days=1)
        captured_only = list(
            (
                await session.execute(
                    select(Transaction)
                    .where(
                        Transaction.user_id == stmt.user_id,
                        Transaction.account_id == stmt.account_id,
                        Transaction.deleted_at.is_(None),
                        Transaction.source != TxnSource.statement,
                        Transaction.statement_id.is_(None),
                        Transaction.posted_at >= lo,
                        Transaction.posted_at < hi,
                    )
                    .order_by(Transaction.posted_at, Transaction.id)
                )
            ).scalars()
        )

    rec = compute_reconciliation(
        stmt.kind or "bank_account", stmt.opening_balance_minor, stmt.closing_balance_minor, lines
    )
    warnings = list((stmt.summary or {}).get("warnings", []))
    return ReconciliationReport(
        statement_id=stmt.id,
        parse_status=stmt.parse_status,
        reconciliation_status=stmt.reconciliation_status,
        kind=stmt.kind,
        currency=stmt.currency,
        account_id=stmt.account_id,
        period_start=stmt.period_start,
        period_end=stmt.period_end,
        balances=ReconBalances(
            opening_minor=stmt.opening_balance_minor,
            closing_minor=stmt.closing_balance_minor,
            computed_closing_minor=rec.computed_closing_minor,
            diff_minor=stmt.diff_minor,
            total_debits_minor=rec.total_debits_minor,
            total_credits_minor=rec.total_credits_minor,
        ),
        counts=ReconCounts(
            lines=len(lines),
            linked=len(linked),
            statement_only=len(statement_only),
            captured_only=len(captured_only),
        ),
        linked=linked,
        statement_only=statement_only,
        captured_only=[TransactionOut.model_validate(t) for t in captured_only],
        warnings=warnings,
    )


def _start_of_day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=DUBAI)

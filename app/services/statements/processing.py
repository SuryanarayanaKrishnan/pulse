"""Statement pipeline: file -> decrypt -> parse -> account -> lines -> reconcile -> link/create txns."""

import hashlib
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import EncryptionError, Encryptor
from app.models.account import Account, AccountType
from app.models.integration import StatementPassword
from app.models.statement import ParseStatus, ReconciliationStatus, Statement, StatementLine
from app.models.transaction import Direction, RawIngest, Transaction, TxnSource
from app.services.categorisation import CategoryResolver, apply_enrichment
from app.services.institutions import institutions
from app.services.merchants import clean_descriptor
from app.services.parsers.dates import DUBAI, at_date, to_dubai_date
from app.services.parsers.statement_base import ParsedStatement
from app.services.parsers.statements import parse_document
from app.services.salary import record_salary
from app.services.statements.documents import (
    DocumentError,
    PasswordInvalid,
    PasswordRequired,
    decrypt_pdf,
    extract_csv,
    extract_pdf,
    sniff_kind,
)
from app.services.statements.matching import DATE_WINDOW_DAYS, Match, match_lines, merchant_score
from app.services.statements.reconcile import compute_reconciliation
from app.services.storage import FileNotFoundInStorage, Storage, StorageError

log = structlog.get_logger(__name__)

_KIND_TO_ACCOUNT_TYPE = {"credit_card": AccountType.credit_card, "bank_account": AccountType.bank}


async def stored_passwords(
    session: AsyncSession, encryptor: Encryptor, user_id: UUID, institution: str | None
) -> list[str]:
    """Passwords to try: the institution's own when known, otherwise every password the user stored."""
    stmt = select(StatementPassword).where(StatementPassword.user_id == user_id)
    if institution:
        stmt = stmt.where(StatementPassword.institution == institution)
    rows = (await session.execute(stmt)).scalars().all()
    return [encryptor.decrypt(r.encrypted_password) for r in rows]


async def resolve_statement_account(
    session: AsyncSession, user_id: UUID, institution: str | None, last4: str | None, kind: str
) -> UUID | None:
    accounts = list(
        (
            await session.execute(
                select(Account).where(Account.user_id == user_id, Account.deleted_at.is_(None))
            )
        ).scalars()
    )
    if institution:
        accounts = [a for a in accounts if institutions.resolve_key(a.institution) == institution]
    if last4:
        by_last4 = [a for a in accounts if a.last4 == last4]
        if len(by_last4) == 1:
            return by_last4[0].id
        if by_last4:
            accounts = by_last4
    typed = [a for a in accounts if a.type == _KIND_TO_ACCOUNT_TYPE[kind]]
    return typed[0].id if len(typed) == 1 else None


def _summary(parsed: ParsedStatement) -> dict[str, Any]:
    return {
        "parser": parsed.parser,
        "last4": parsed.last4,
        "statement_date": parsed.statement_date.isoformat() if parsed.statement_date else None,
        "due_date": parsed.due_date.isoformat() if parsed.due_date else None,
        "min_due_minor": parsed.min_due_minor,
        "total_payments_minor": parsed.total_payments_minor,
        "total_purchases_minor": parsed.total_purchases_minor,
        "line_count": len(parsed.lines),
        "warnings": parsed.warnings[:50],
    }


async def process_statement(
    session: AsyncSession, statement_id: UUID, storage: Storage, encryptor: Encryptor | None
) -> ParseStatus:
    """Run the whole pipeline for one statement row. Idempotent: safe to re-run (reprocess)."""
    stmt = await session.get(Statement, statement_id)
    if stmt is None or stmt.deleted_at is not None:
        log.warning("statement_missing", statement_id=str(statement_id))
        return ParseStatus.failed

    async def finish(status: ParseStatus, error: str | None = None) -> ParseStatus:
        stmt.parse_status = status
        stmt.parse_error = error
        await session.commit()
        log.info("statement_processed", statement_id=str(statement_id), status=status.value, error=error)
        return status

    if not stmt.file_storage_key:
        return await finish(ParseStatus.failed, "No stored file")
    try:
        data = await storage.get(stmt.file_storage_key)
    except FileNotFoundInStorage:
        return await finish(ParseStatus.failed, "Stored file is missing")
    except StorageError as exc:
        # Transient storage trouble: let arq retry rather than marking the statement failed.
        raise RuntimeError(f"storage unavailable: {exc}") from exc

    kind = sniff_kind(data)
    try:
        if kind == "pdf":
            passwords: list[str] = []
            if encryptor is not None:
                passwords = await stored_passwords(session, encryptor, stmt.user_id, stmt.institution)
            doc = extract_pdf(decrypt_pdf(data, passwords))
        elif kind == "csv":
            doc = extract_csv(data)
        else:
            return await finish(ParseStatus.failed, "Unsupported file type (expected PDF or CSV)")
    except PasswordRequired:
        return await finish(
            ParseStatus.needs_password, "PDF is password protected: set the statement password"
        )
    except PasswordInvalid:
        return await finish(ParseStatus.needs_password, "Stored statement password did not open the PDF")
    except EncryptionError as exc:
        return await finish(ParseStatus.failed, f"Stored password could not be decrypted: {exc}")
    except DocumentError as exc:
        return await finish(ParseStatus.failed, str(exc))

    outcome = parse_document(doc, institution=stmt.institution)
    parsed = outcome.statement
    if parsed is None:
        return await finish(
            ParseStatus.failed, f"No transactions recognised (tried {', '.join(outcome.attempts)})"
        )

    stmt.institution = stmt.institution or outcome.institution
    stmt.kind = parsed.kind
    stmt.currency = parsed.currency
    stmt.period_start, stmt.period_end = parsed.period_start, parsed.period_end
    stmt.opening_balance_minor, stmt.closing_balance_minor = parsed.opening_minor, parsed.closing_minor
    stmt.summary = _summary(parsed)

    rec = compute_reconciliation(parsed.kind, parsed.opening_minor, parsed.closing_minor, parsed.lines)
    stmt.reconciliation_status = ReconciliationStatus(rec.status)
    stmt.diff_minor = rec.diff_minor

    # Replace lines (reprocessing must not duplicate them).
    await session.execute(delete(StatementLine).where(StatementLine.statement_id == stmt.id))
    line_rows = [
        StatementLine(
            user_id=stmt.user_id,
            statement_id=stmt.id,
            line_no=i,
            posted_on=ln.posted_on,
            description=ln.description[:2000],
            amount_minor=ln.amount_minor,
            currency=ln.currency,
            direction=ln.direction.value,
            foreign_amount_minor=ln.foreign_amount_minor,
            foreign_currency=ln.foreign_currency,
            balance_minor=ln.balance_minor,
        )
        for i, ln in enumerate(parsed.lines)
    ]
    session.add_all(line_rows)

    if stmt.account_id is None:
        stmt.account_id = await resolve_statement_account(
            session, stmt.user_id, stmt.institution, parsed.last4, parsed.kind
        )
    if stmt.account_id is None:
        await session.flush()
        return await finish(
            ParseStatus.needs_review, "Could not tell which account this statement belongs to"
        )

    await session.flush()
    await link_and_create(session, stmt, line_rows)
    return await finish(ParseStatus.parsed)


def _hash(user_id: UUID, account_id: UUID, line: StatementLine, occurrence: int) -> str:
    basis = "|".join(
        [
            "stmt",
            str(user_id),
            str(account_id),
            line.posted_on.isoformat(),
            str(line.amount_minor),
            line.direction,
            clean_descriptor(line.description).lower(),
            str(occurrence),
        ]
    )
    return hashlib.sha256(basis.encode()).hexdigest()


async def link_and_create(session: AsyncSession, stmt: Statement, lines: list[StatementLine]) -> None:
    """Link lines to captured transactions, create the rest (deduped by external_hash)."""
    assert stmt.account_id is not None
    if not lines:
        return
    # A reprocess may match differently: drop this statement's old links to captured transactions first.
    await session.execute(
        update(Transaction)
        .where(
            Transaction.user_id == stmt.user_id,
            Transaction.statement_id == stmt.id,
            Transaction.source != TxnSource.statement,
        )
        .values(statement_id=None)
    )
    lo = min(ln.posted_on for ln in lines) - timedelta(days=DATE_WINDOW_DAYS + 1)
    hi = max(ln.posted_on for ln in lines) + timedelta(days=DATE_WINDOW_DAYS + 2)
    candidates = list(
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.user_id == stmt.user_id,
                    Transaction.account_id == stmt.account_id,
                    Transaction.deleted_at.is_(None),
                    Transaction.source != TxnSource.statement,
                    (Transaction.statement_id.is_(None)) | (Transaction.statement_id == stmt.id),
                    Transaction.posted_at >= datetime(lo.year, lo.month, lo.day, tzinfo=UTC),
                    Transaction.posted_at < datetime(hi.year, hi.month, hi.day, tzinfo=UTC),
                )
            )
        ).scalars()
    )
    matches: list[Match] = match_lines(lines, candidates)
    matched = {m.line_index: m for m in matches}
    resolver = await CategoryResolver.load(session, stmt.user_id)

    occurrences: Counter[tuple[str, str, int, str]] = Counter()
    for i, line in enumerate(lines):
        if (m := matched.get(i)) is not None:
            captured = candidates[m.txn_index]
            lock_to_statement(captured, line, stmt.id)
            if captured.category_id is None:
                apply_enrichment(
                    captured,
                    resolver.enrich(
                        line.description,
                        Direction(line.direction),
                        account_id=captured.account_id,
                        amount_minor=captured.amount_minor,
                        source=captured.source,
                        notes=captured.notes,
                    ),
                    set_merchant=False,  # keep the merchant name the SMS gave us
                )
            line.transaction_id, line.match_status, line.match_score = captured.id, "linked", m.score
            continue
        key = (
            line.posted_on.isoformat(),
            line.direction,
            line.amount_minor,
            clean_descriptor(line.description).lower(),
        )
        occ = occurrences[key]
        occurrences[key] += 1
        ext_hash = _hash(stmt.user_id, stmt.account_id, line, occ)
        existing = (
            await session.execute(
                select(Transaction).where(
                    Transaction.user_id == stmt.user_id, Transaction.external_hash == ext_hash
                )
            )
        ).scalar_one_or_none()
        txn: Transaction
        if existing is None:
            enriched = resolver.enrich(
                line.description,
                Direction(line.direction),
                account_id=stmt.account_id,
                amount_minor=line.amount_minor,
                source=TxnSource.statement,
            )
            txn = Transaction(
                user_id=stmt.user_id,
                account_id=stmt.account_id,
                statement_id=stmt.id,
                sources=["statement"],
                statement_locked=True,
                category_id=enriched.category_id,
                exclude_from_budget=enriched.exclude_from_budget,
                tags=list(enriched.tags),
                applied_rule_id=enriched.rule_id,
                amount_minor=line.amount_minor,
                currency=line.currency,
                direction=Direction(line.direction),
                merchant_raw=line.description,
                merchant_normalized=enriched.merchant_normalized,
                posted_at=at_date(line.posted_on),
                source=TxnSource.statement,
                external_hash=ext_hash,
            )
            try:
                async with session.begin_nested():
                    session.add(txn)
                    await session.flush()
                    await record_salary(session, txn)
            except IntegrityError:
                txn = (
                    await session.execute(
                        select(Transaction).where(
                            Transaction.user_id == stmt.user_id, Transaction.external_hash == ext_hash
                        )
                    )
                ).scalar_one()
        else:
            txn = existing
            if txn.deleted_at is None and txn.statement_id is None:
                txn.statement_id = stmt.id
        line.transaction_id, line.match_status, line.match_score = txn.id, "created", None
    await session.flush()


def lock_to_statement(txn: Transaction, line: StatementLine, statement_id: UUID) -> None:
    """A statement line confirms a captured transaction: the bank's amount, currency and date win and the
    row is final (``statement_locked``). Everything captured about it (merchant, tags, category) is kept."""
    txn.statement_id = statement_id
    txn.statement_locked = True
    if txn.amount_minor != line.amount_minor or txn.currency != line.currency:
        txn.amount_minor, txn.currency = line.amount_minor, line.currency
    txn.posted_at = at_date(line.posted_on, near=txn.posted_at)
    if not txn.merchant_raw:
        txn.merchant_raw = line.description
    if "statement" not in (txn.sources or []):
        txn.sources = [*(txn.sources or []), "statement"]


async def find_statement_transaction(
    session: AsyncSession,
    user_id: UUID,
    account_id: UUID,
    *,
    amount_minor: int,
    currency: str,
    direction: Direction,
    merchant: str | None,
    posted_at: datetime,
) -> Transaction | None:
    """Late-arriving SMS: is there a statement-created transaction for it already? (avoid duplicating)"""
    day = to_dubai_date(posted_at)
    lo = datetime(day.year, day.month, day.day, tzinfo=DUBAI) - timedelta(days=DATE_WINDOW_DAYS)
    hi = datetime(day.year, day.month, day.day, tzinfo=DUBAI) + timedelta(days=DATE_WINDOW_DAYS + 1)
    rows = (
        await session.execute(
            select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.account_id == account_id,
                Transaction.deleted_at.is_(None),
                Transaction.source == TxnSource.statement,
                Transaction.amount_minor == amount_minor,
                Transaction.currency == currency,
                Transaction.direction == direction,
                Transaction.posted_at >= lo,
                Transaction.posted_at < hi,
            )
        )
    ).scalars()
    best: tuple[int, Transaction] | None = None
    for txn in rows:
        claimed = (
            await session.execute(select(RawIngest.id).where(RawIngest.transaction_id == txn.id).limit(1))
        ).first()
        if claimed:
            continue  # another message already maps to this statement line
        score = merchant_score(merchant, txn.merchant_normalized or txn.merchant_raw)
        acceptable = (score is not None and score >= 60) or (score is None and merchant is None)
        if acceptable and (best is None or (score or 0) > best[0]):
            best = (score or 0, txn)
    return best[1] if best else None

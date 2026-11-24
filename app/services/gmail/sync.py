"""Pull bank statement PDFs from Gmail into ``statements`` (idempotent per message + attachment)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import Encryptor
from app.models.integration import EmailConnection
from app.models.statement import Statement
from app.services.gmail.client import GmailApiError, GmailClient, pdf_attachments, sender_address
from app.services.gmail.oauth import InvalidGrant, refresh_access_token
from app.services.institutions import Institution, institutions
from app.services.statements.documents import sniff_kind
from app.services.statements.intake import create_statement
from app.services.storage import Storage

log = structlog.get_logger(__name__)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
Enqueue = Callable[[UUID], Awaitable[None]]


@dataclass
class SyncResult:
    status: str = "ok"  # ok | needs_reauth | partial | error
    messages_seen: int = 0
    statements_created: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def build_query(inst: Institution, after: date) -> str:
    """Gmail search for one institution's statement emails, e.g.

    ``has:attachment filename:pdf (from:emiratesnbd.com) (subject:"statement" OR ...) after:2026/01/01``
    """
    senders = " OR ".join(f"from:{d}" for d in inst.email_domains)
    subjects = " OR ".join(f'subject:"{t}"' for t in inst.subject_terms)
    parts = ["has:attachment", "filename:pdf", f"({senders})", f"({subjects})", f"after:{after:%Y/%m/%d}"]
    if inst.extra_query:
        parts.append(inst.extra_query)
    return " ".join(parts)


async def sync_connection(
    session: AsyncSession,
    conn: EmailConnection,
    *,
    settings: Settings,
    encryptor: Encryptor,
    storage: Storage,
    http: httpx.AsyncClient,
    enqueue: Enqueue,
    now: datetime | None = None,
) -> SyncResult:
    started = now or datetime.now(UTC)
    result = SyncResult()
    try:
        access = await refresh_access_token(http, settings, encryptor.decrypt(conn.encrypted_refresh_token))
    except InvalidGrant as exc:
        conn.status, conn.last_error = "needs_reauth", f"Google rejected the stored token: {exc}"
        await session.commit()
        result.status = "needs_reauth"
        return result
    gmail = GmailClient(http, access)

    after_dt = (
        conn.last_synced_at - timedelta(days=settings.gmail_sync_overlap_days)
        if conn.last_synced_at
        else started - timedelta(days=settings.gmail_initial_lookback_days)
    )
    budget = settings.gmail_max_messages_per_sync
    for inst in institutions:
        if not inst.email_domains or budget <= 0:
            continue
        try:
            ids = await gmail.search(build_query(inst, after_dt.date()), limit=budget)
        except GmailApiError as exc:
            if exc.status in (401, 403):
                conn.status, conn.last_error = "needs_reauth", str(exc)
                await session.commit()
                result.status = "needs_reauth"
                return result
            result.errors.append(f"{inst.key}: {exc}")
            continue
        budget -= len(ids)
        for message_id in ids:
            result.messages_seen += 1
            try:
                await _import_message(session, conn, gmail, inst, message_id, storage, enqueue, result)
            except GmailApiError as exc:
                result.errors.append(f"{inst.key}/{message_id}: {exc}")
            except Exception as exc:  # one bad message must not stop the rest of the mailbox
                log.exception("gmail_message_failed", message_id=message_id)
                result.errors.append(f"{inst.key}/{message_id}: {type(exc).__name__}")

    conn.last_error = "; ".join(result.errors)[:1000] or None
    conn.status = "active"
    if result.errors:
        result.status = "partial"  # keep last_synced_at so the next run re-covers the window
    else:
        conn.last_synced_at = started
    await session.commit()
    return result


async def _import_message(
    session: AsyncSession,
    conn: EmailConnection,
    gmail: GmailClient,
    inst: Institution,
    message_id: str,
    storage: Storage,
    enqueue: Enqueue,
    result: SyncResult,
) -> None:
    message = await gmail.get_message(message_id)
    # Defence in depth: the query filters on sender, but display names are spoofable, so check the address.
    if institutions.from_email_address(sender_address(message)) != inst.key:
        result.skipped += 1
        return
    attachments = list(pdf_attachments(message, MAX_ATTACHMENT_BYTES))
    if not attachments:
        result.skipped += 1
        return
    for att in attachments:
        external_id = f"gmail:{message_id}:{att.index}"
        known = await session.execute(
            select(Statement.id).where(
                Statement.user_id == conn.user_id, Statement.external_id == external_id
            )
        )
        if known.first() is not None:  # already imported (possibly since deleted): do not even download
            result.duplicates += 1
            continue
        data = await gmail.get_attachment(message_id, att.attachment_id)
        if sniff_kind(data) != "pdf":
            result.skipped += 1
            continue
        stmt, duplicate = await create_statement(
            session,
            storage,
            conn.user_id,
            data,
            kind="pdf",
            filename=att.filename,
            content_type="application/pdf",
            source="gmail",
            institution=inst.key,
            email_connection_id=conn.id,
            external_id=external_id,
        )
        await session.commit()
        if duplicate:
            result.duplicates += 1
        else:
            result.statements_created += 1
            await enqueue(stmt.id)

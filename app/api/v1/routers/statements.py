from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from sqlalchemy import select, update

from app.api.deps import CurrentUser, QueueDep, SessionDep, SettingsDep, StorageDep
from app.api.utils import get_owned
from app.core.errors import AppError, DomainValidationError, NotFoundError
from app.core.queue import QueuePool
from app.models.account import Account
from app.models.statement import ParseStatus, Statement
from app.models.transaction import Transaction, TxnSource
from app.schemas.statement import (
    ReconciliationReport,
    ReprocessRequest,
    StatementOut,
    StatementUploadOut,
)
from app.services.audit import record_audit
from app.services.institutions import institutions
from app.services.statements.documents import sniff_kind
from app.services.statements.intake import create_statement
from app.services.statements.report import build_report
from app.services.storage import FileNotFoundInStorage

router = APIRouter(prefix="/statements", tags=["statements"])
log = structlog.get_logger(__name__)


class UnsupportedFile(AppError):
    status_code = 415
    code = "unsupported_file"


class TooLarge(AppError):
    status_code = 413
    code = "file_too_large"


async def enqueue_processing(queue: QueuePool, statement_id: UUID) -> None:
    try:
        # Unique job id: arq refuses to re-enqueue an id whose previous result is still kept.
        await queue.enqueue_job(
            "process_statement", str(statement_id), _job_id=f"statement:{statement_id}:{uuid4().hex[:8]}"
        )
    except Exception:
        # The row stays `pending`; a manual reprocess (or a future sweeper) picks it up.
        log.exception("statement_enqueue_failed", statement_id=str(statement_id))


@router.post("/upload", response_model=StatementUploadOut, status_code=202)
async def upload_statement(
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
    storage: StorageDep,
    settings: SettingsDep,
    response: Response,
    file: Annotated[UploadFile, File(description="PDF or CSV statement")],
    institution: Annotated[
        str | None, Form(description="Institution key/name; needed to unlock PDFs")
    ] = None,
    account_id: Annotated[UUID | None, Form()] = None,
) -> StatementUploadOut:
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise TooLarge(f"File exceeds {settings.max_upload_bytes} bytes")
    if not data:
        raise DomainValidationError("Empty file", code="empty_file")
    kind = sniff_kind(data)
    if kind not in ("pdf", "csv"):
        raise UnsupportedFile("Only PDF and CSV statements are supported")
    inst_key = None
    if institution:
        inst_key = institutions.resolve_key(institution)
        if inst_key is None:
            raise DomainValidationError(
                "Unknown institution", code="unknown_institution", details=[{"valid": institutions.keys}]
            )
    if account_id:
        await get_owned(session, Account, account_id, user.id)

    stmt, duplicate = await create_statement(
        session,
        storage,
        user.id,
        data,
        kind=kind,
        filename=file.filename,
        content_type=file.content_type,
        source="upload",
        institution=inst_key,
        account_id=account_id,
    )
    await session.commit()
    if duplicate:
        response.status_code = 200
    else:
        await enqueue_processing(queue, stmt.id)
    return StatementUploadOut(id=stmt.id, duplicate=duplicate, parse_status=stmt.parse_status)


@router.get("", response_model=list[StatementOut])
async def list_statements(
    user: CurrentUser,
    session: SessionDep,
    account_id: UUID | None = None,
    parse_status: ParseStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Statement]:
    stmt = select(Statement).where(Statement.user_id == user.id, Statement.deleted_at.is_(None))
    if account_id:
        stmt = stmt.where(Statement.account_id == account_id)
    if parse_status:
        stmt = stmt.where(Statement.parse_status == parse_status)
    stmt = stmt.order_by(Statement.created_at.desc(), Statement.id.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())


@router.get("/{statement_id}", response_model=StatementOut)
async def get_statement(statement_id: UUID, user: CurrentUser, session: SessionDep) -> Statement:
    return await get_owned(session, Statement, statement_id, user.id)


@router.get("/{statement_id}/file")
async def download_statement_file(
    statement_id: UUID, user: CurrentUser, session: SessionDep, storage: StorageDep
) -> Response:
    """The original statement exactly as the bank sent it (a locked PDF stays locked).

    The phone pulls emailed statements through this and reads them on the device with the passwords
    saved there, so the numbers reach the local database without a plaintext copy being sent.
    """
    stmt = await get_owned(session, Statement, statement_id, user.id)
    if not stmt.file_storage_key:
        raise NotFoundError("This statement has no stored file")
    try:
        data = await storage.get(stmt.file_storage_key)
    except FileNotFoundInStorage as exc:
        raise NotFoundError("The stored statement file is missing") from exc
    return Response(
        content=data,
        media_type=stmt.content_type or "application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{statement_id}/reconciliation", response_model=ReconciliationReport)
async def get_reconciliation(
    statement_id: UUID, user: CurrentUser, session: SessionDep
) -> ReconciliationReport:
    stmt = await get_owned(session, Statement, statement_id, user.id)
    return await build_report(session, stmt)


@router.post("/{statement_id}/reprocess", response_model=StatementOut, status_code=202)
async def reprocess_statement(
    statement_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
    payload: ReprocessRequest | None = None,
) -> Statement:
    """Re-run parsing (e.g. after setting the PDF password or choosing the account)."""
    stmt = await get_owned(session, Statement, statement_id, user.id)
    if payload and payload.account_id:
        await get_owned(session, Account, payload.account_id, user.id)
        stmt.account_id = payload.account_id
    stmt.parse_status = ParseStatus.pending
    stmt.parse_error = None
    await session.commit()
    await session.refresh(stmt)
    await enqueue_processing(queue, stmt.id)
    return stmt


@router.delete("/{statement_id}", status_code=204)
async def delete_statement(statement_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    """Soft-delete; transactions created from the statement go with it, linked captures are unlinked."""
    stmt = await get_owned(session, Statement, statement_id, user.id)
    now = datetime.now(UTC)
    await session.execute(
        update(Transaction)
        .where(
            Transaction.user_id == user.id,
            Transaction.statement_id == stmt.id,
            Transaction.source == TxnSource.statement,
        )
        .values(deleted_at=now)
    )
    await session.execute(
        update(Transaction)
        .where(Transaction.user_id == user.id, Transaction.statement_id == stmt.id)
        .values(statement_id=None)
    )
    stmt.deleted_at = now
    record_audit(
        session, user_id=user.id, action="statement.delete", entity_type="statement", entity_id=stmt.id
    )
    await session.commit()
    return Response(status_code=204)

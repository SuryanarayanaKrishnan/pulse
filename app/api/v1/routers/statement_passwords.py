from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, EncryptorDep, QueueDep, SessionDep
from app.api.v1.routers.statements import enqueue_processing
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.queue import QueuePool
from app.models.integration import StatementPassword
from app.models.statement import ParseStatus, Statement
from app.schemas.statement import StatementPasswordIn, StatementPasswordOut
from app.services.audit import record_audit
from app.services.institutions import institutions

router = APIRouter(prefix="/statement-passwords", tags=["statements"])
log = structlog.get_logger(__name__)


def _key(raw: str) -> str:
    key = institutions.resolve_key(raw)
    if key is None:
        raise DomainValidationError(
            "Unknown institution", code="unknown_institution", details=[{"valid": institutions.keys}]
        )
    return key


def _out(institution: str, updated_at: datetime) -> StatementPasswordOut:
    inst = institutions.get(institution)
    return StatementPasswordOut(
        institution=institution, institution_name=inst.name if inst else institution, updated_at=updated_at
    )


async def _retry_locked_statements(
    session: AsyncSession, queue: QueuePool, user_id: UUID, institution: str
) -> None:
    """Statements parked as needs_password for this institution (or of unknown institution) get retried."""
    rows = (
        (
            await session.execute(
                select(Statement.id).where(
                    Statement.user_id == user_id,
                    Statement.deleted_at.is_(None),
                    Statement.parse_status == ParseStatus.needs_password,
                    (Statement.institution == institution) | Statement.institution.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if rows:
        await session.execute(
            update(Statement)
            .where(Statement.id.in_(rows))
            .values(parse_status=ParseStatus.pending, parse_error=None)
        )
    await session.commit()
    for sid in rows:
        await enqueue_processing(queue, sid)


async def _store(
    payload: StatementPasswordIn,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
    enc: EncryptorDep,
    *,
    must_be_new: bool,
) -> StatementPasswordOut:
    key = _key(payload.institution)
    row = (
        await session.execute(
            select(StatementPassword).where(
                StatementPassword.user_id == user.id, StatementPassword.institution == key
            )
        )
    ).scalar_one_or_none()
    if row is not None and must_be_new:
        raise ConflictError("A password is already stored for this institution; use PUT to replace it")
    if row is None:
        row = StatementPassword(
            user_id=user.id, institution=key, encrypted_password=enc.encrypt(payload.password)
        )
        session.add(row)
    else:
        row.encrypted_password = enc.encrypt(payload.password)
    record_audit(
        session, user_id=user.id, action="statement_password.set", entity_type="institution", entity_id=key
    )
    await session.commit()
    await session.refresh(row)
    await _retry_locked_statements(session, queue, user.id, key)
    return _out(key, row.updated_at)


@router.post("", response_model=StatementPasswordOut, status_code=201)
async def create_password(
    payload: StatementPasswordIn, user: CurrentUser, session: SessionDep, queue: QueueDep, enc: EncryptorDep
) -> StatementPasswordOut:
    return await _store(payload, user, session, queue, enc, must_be_new=True)


@router.put("", response_model=StatementPasswordOut)
async def put_password(
    payload: StatementPasswordIn, user: CurrentUser, session: SessionDep, queue: QueueDep, enc: EncryptorDep
) -> StatementPasswordOut:
    """Create or replace the password for an institution."""
    return await _store(payload, user, session, queue, enc, must_be_new=False)


@router.get("", response_model=list[StatementPasswordOut])
async def list_passwords(user: CurrentUser, session: SessionDep) -> list[StatementPasswordOut]:
    rows = (
        await session.execute(
            select(StatementPassword)
            .where(StatementPassword.user_id == user.id)
            .order_by(StatementPassword.institution)
        )
    ).scalars()
    return [_out(r.institution, r.updated_at) for r in rows]


@router.delete("/{institution}", status_code=204)
async def delete_password(institution: str, user: CurrentUser, session: SessionDep) -> Response:
    key = _key(institution)
    row = (
        await session.execute(
            select(StatementPassword).where(
                StatementPassword.user_id == user.id, StatementPassword.institution == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("No password stored for this institution")
    await session.delete(row)
    record_audit(
        session, user_id=user.id, action="statement_password.delete", entity_type="institution", entity_id=key
    )
    await session.commit()
    return Response(status_code=204)

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.api.utils import apply_updates, get_owned
from app.core.errors import DomainValidationError, NotFoundError
from app.models.asset import Asset
from app.models.portfolio import Document, DocumentReminder
from app.schemas.documents import (
    DocBackupIn,
    DocumentCreate,
    DocumentOut,
    DocumentUpdate,
    ReminderIn,
    ReminderOut,
)
from app.services.insights_service import today_dubai

router = APIRouter(prefix="/documents", tags=["documents"])


def _reminder_out(r: DocumentReminder, today: date) -> ReminderOut:
    return ReminderOut(
        id=r.id,
        created_at=r.created_at,
        updated_at=r.updated_at,
        document_id=r.document_id,
        kind=r.kind,
        label=r.label,
        due_on=r.due_on,
        remind_days_before=r.remind_days_before,
        done_at=r.done_at,
        days_left=(r.due_on - today).days,
    )


async def _reminders(session: SessionDep, document_ids: list[UUID]) -> dict[UUID, list[DocumentReminder]]:
    out: dict[UUID, list[DocumentReminder]] = {i: [] for i in document_ids}
    if document_ids:
        rows = await session.execute(
            select(DocumentReminder)
            .where(DocumentReminder.document_id.in_(document_ids))
            .order_by(DocumentReminder.due_on)
        )
        for r in rows.scalars():
            out[r.document_id].append(r)
    return out


def _out(d: Document, reminders: list[DocumentReminder]) -> DocumentOut:
    today = today_dubai()
    open_due = [r.due_on for r in reminders if r.done_at is None]
    return DocumentOut(
        id=d.id,
        created_at=d.created_at,
        updated_at=d.updated_at,
        kind=d.kind,
        title=d.title,
        institution=d.institution,
        reference=d.reference,
        amount_minor=d.amount_minor,
        currency=d.currency,
        asset_id=d.asset_id,
        notes=d.notes,
        has_backup=d.storage_key is not None,
        backup_size=d.blob_size,
        reminders=[_reminder_out(r, today) for r in reminders],
        next_due_on=min(open_due) if open_due else None,
    )


async def _doc(session: SessionDep, doc_id: UUID, user_id: UUID) -> Document:
    return await get_owned(session, Document, doc_id, user_id)


@router.post("", response_model=DocumentOut, status_code=201)
async def create_document(payload: DocumentCreate, user: CurrentUser, session: SessionDep) -> DocumentOut:
    """Metadata only (title deed, FD certificate, insurance...). Files stay on the device."""
    if payload.asset_id is not None:
        try:
            await get_owned(session, Asset, payload.asset_id, user.id)
        except NotFoundError as exc:
            raise DomainValidationError("Unknown asset", code="invalid_asset") from exc
    data = payload.model_dump(exclude={"reminders"})
    d = Document(user_id=user.id, **data)
    session.add(d)
    await session.flush()
    for r in payload.reminders:
        session.add(DocumentReminder(user_id=user.id, document_id=d.id, **r.model_dump()))
    await session.commit()
    await session.refresh(d)
    return _out(d, (await _reminders(session, [d.id]))[d.id])


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: CurrentUser, session: SessionDep) -> list[DocumentOut]:
    docs = list(
        (
            await session.execute(
                select(Document)
                .where(Document.user_id == user.id, Document.deleted_at.is_(None))
                .order_by(Document.created_at)
            )
        ).scalars()
    )
    rem = await _reminders(session, [d.id for d in docs])
    return [_out(d, rem[d.id]) for d in docs]


@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(doc_id: UUID, user: CurrentUser, session: SessionDep) -> DocumentOut:
    d = await _doc(session, doc_id, user.id)
    return _out(d, (await _reminders(session, [d.id]))[d.id])


@router.patch("/{doc_id}", response_model=DocumentOut)
async def update_document(
    doc_id: UUID, payload: DocumentUpdate, user: CurrentUser, session: SessionDep
) -> DocumentOut:
    d = await _doc(session, doc_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("title", "") is None:
        data.pop("title")
    apply_updates(d, data)
    await session.commit()
    await session.refresh(d)
    return _out(d, (await _reminders(session, [d.id]))[d.id])


@router.put("/{doc_id}/backup", response_model=DocumentOut)
async def set_backup(
    doc_id: UUID, payload: DocBackupIn, user: CurrentUser, session: SessionDep
) -> DocumentOut:
    """Metadata of an encrypted backup blob (the server never stores or sees the plaintext)."""
    if not payload.encrypted:
        raise DomainValidationError("Only encrypted backups are tracked", code="backup_not_encrypted")
    d = await _doc(session, doc_id, user.id)
    d.storage_key, d.blob_size, d.blob_sha256, d.blob_encrypted = (
        payload.storage_key,
        payload.size,
        payload.sha256,
        True,
    )
    await session.commit()
    await session.refresh(d)
    return _out(d, (await _reminders(session, [d.id]))[d.id])


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: UUID, user: CurrentUser, session: SessionDep) -> Response:
    d = await _doc(session, doc_id, user.id)
    d.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


@router.post("/{doc_id}/reminders", response_model=ReminderOut, status_code=201)
async def add_reminder(
    doc_id: UUID, payload: ReminderIn, user: CurrentUser, session: SessionDep
) -> ReminderOut:
    d = await _doc(session, doc_id, user.id)
    r = DocumentReminder(user_id=user.id, document_id=d.id, **payload.model_dump())
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return _reminder_out(r, today_dubai())


async def _reminder(session: SessionDep, doc_id: UUID, reminder_id: UUID, user_id: UUID) -> DocumentReminder:
    await _doc(session, doc_id, user_id)
    r = (
        await session.execute(
            select(DocumentReminder).where(
                DocumentReminder.id == reminder_id, DocumentReminder.document_id == doc_id
            )
        )
    ).scalar_one_or_none()
    if r is None:
        raise NotFoundError("Reminder not found")
    return r


@router.post("/{doc_id}/reminders/{reminder_id}/done", response_model=ReminderOut)
async def complete_reminder(
    doc_id: UUID, reminder_id: UUID, user: CurrentUser, session: SessionDep
) -> ReminderOut:
    r = await _reminder(session, doc_id, reminder_id, user.id)
    r.done_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(r)
    return _reminder_out(r, today_dubai())


@router.delete("/{doc_id}/reminders/{reminder_id}", status_code=204)
async def delete_reminder(
    doc_id: UUID, reminder_id: UUID, user: CurrentUser, session: SessionDep
) -> Response:
    r = await _reminder(session, doc_id, reminder_id, user.id)
    await session.delete(r)
    await session.commit()
    return Response(status_code=204)

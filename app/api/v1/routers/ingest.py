import structlog
from fastapi import APIRouter, Response

from app.api.deps import CurrentUser, QueueDep, SessionDep
from app.models.transaction import TxnSource
from app.schemas.ingest import IngestOut, IngestRequest
from app.services.ingest import store_raw

router = APIRouter(prefix="/ingest", tags=["ingest"])
log = structlog.get_logger(__name__)


async def _ingest(
    channel: TxnSource,
    req: IngestRequest,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
    response: Response,
) -> IngestOut:
    row, duplicate = await store_raw(session, user.id, channel, req)
    await session.commit()
    if duplicate:
        response.status_code = 200
    else:
        response.status_code = 202
        try:
            await queue.enqueue_job("process_ingest", str(row.id), _job_id=f"ingest:{row.id}")
        except Exception:
            # The raw payload is safely stored; a sweeper can re-enqueue pending rows later.
            log.exception("ingest_enqueue_failed", ingest_id=str(row.id))
    return IngestOut(id=row.id, duplicate=duplicate, parse_status=row.parse_status)


@router.post("/sms", response_model=IngestOut, status_code=202)
async def ingest_sms(
    req: IngestRequest, user: CurrentUser, session: SessionDep, queue: QueueDep, response: Response
) -> IngestOut:
    return await _ingest(TxnSource.sms, req, user, session, queue, response)


@router.post("/notification", response_model=IngestOut, status_code=202)
async def ingest_notification(
    req: IngestRequest, user: CurrentUser, session: SessionDep, queue: QueueDep, response: Response
) -> IngestOut:
    return await _ingest(TxnSource.notification, req, user, session, queue, response)


@router.post("/apple-shortcut", response_model=IngestOut, status_code=202)
async def ingest_apple_shortcut(
    req: IngestRequest, user: CurrentUser, session: SessionDep, queue: QueueDep, response: Response
) -> IngestOut:
    return await _ingest(TxnSource.apple_shortcut, req, user, session, queue, response)

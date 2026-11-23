from typing import Any
from uuid import UUID

import structlog

from app.services.ingest import process_raw_ingest

log = structlog.get_logger(__name__)


async def process_ingest(ctx: dict[str, Any], ingest_id: str) -> str:
    """arq job: parse a stored raw ingest payload into a transaction."""
    session_factory = ctx["session_factory"]
    async with session_factory() as session:
        status = await process_raw_ingest(session, UUID(ingest_id))
    log.info("ingest_processed", ingest_id=ingest_id, status=status.value)
    return status.value

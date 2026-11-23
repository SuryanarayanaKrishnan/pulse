from typing import Any
from uuid import UUID

import structlog

from app.core.crypto import EncryptionError, get_encryptor
from app.services.ingest import process_raw_ingest
from app.services.statements.processing import process_statement as run_statement_pipeline
from app.services.storage import get_storage

log = structlog.get_logger(__name__)


async def process_ingest(ctx: dict[str, Any], ingest_id: str) -> str:
    """arq job: parse a stored raw ingest payload into a transaction."""
    session_factory = ctx["session_factory"]
    async with session_factory() as session:
        status = await process_raw_ingest(session, UUID(ingest_id))
    log.info("ingest_processed", ingest_id=ingest_id, status=status.value)
    return status.value


async def process_statement(ctx: dict[str, Any], statement_id: str) -> str:
    """arq job: decrypt, parse, reconcile and link/create transactions for one statement."""
    try:
        encryptor = get_encryptor()
    except EncryptionError:
        encryptor = None  # unencrypted statements still work; locked PDFs end up needs_password
    storage = ctx.get("storage") or get_storage()
    async with ctx["session_factory"]() as session:
        status = await run_statement_pipeline(session, UUID(statement_id), storage, encryptor)
    return status.value

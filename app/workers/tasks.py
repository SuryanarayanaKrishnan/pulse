from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import EncryptionError, get_encryptor
from app.models.integration import EmailConnection
from app.services.gmail.sync import sync_connection
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


async def sync_gmail_connection(ctx: dict[str, Any], connection_id: str) -> dict[str, Any]:
    """arq job: import new statement PDFs from one Gmail connection (idempotent per message+attachment)."""
    settings = get_settings()
    storage = ctx.get("storage") or get_storage()
    http: httpx.AsyncClient | None = ctx.get("http")
    owns_http = http is None
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    redis = ctx.get("redis")

    async def enqueue(statement_id: UUID) -> None:
        if redis is not None:
            await redis.enqueue_job(
                "process_statement", str(statement_id), _job_id=f"statement:{statement_id}:{uuid4().hex[:8]}"
            )

    try:
        async with ctx["session_factory"]() as session:
            conn = await session.get(EmailConnection, UUID(connection_id))
            if conn is None or conn.deleted_at is not None or conn.provider != "gmail":
                return {"status": "skipped"}
            result = await sync_connection(
                session,
                conn,
                settings=settings,
                encryptor=get_encryptor(),
                storage=storage,
                http=http,
                enqueue=enqueue,
            )
    finally:
        if owns_http:
            await http.aclose()
    log.info("gmail_synced", connection_id=connection_id, **result.__dict__)
    return {"status": result.status, "created": result.statements_created, "duplicates": result.duplicates}


async def sync_all_gmail_connections(ctx: dict[str, Any]) -> int:
    """arq cron: fan out one `sync_gmail_connection` job per active connection."""
    redis = ctx["redis"]
    bucket = datetime.now(UTC).strftime("%Y%m%d%H")  # at most one queued job per connection per hour
    async with ctx["session_factory"]() as session:
        rows = await session.execute(
            select(EmailConnection.id).where(
                EmailConnection.provider == "gmail",
                EmailConnection.deleted_at.is_(None),
                EmailConnection.status == "active",
            )
        )
        ids = list(rows.scalars())
    for conn_id in ids:
        await redis.enqueue_job(
            "sync_gmail_connection", str(conn_id), _job_id=f"gmail-sync:{conn_id}:{bucket}"
        )
    return len(ids)


async def snapshot_net_worth(ctx: dict[str, Any]) -> dict[str, int]:
    """arq cron: store today's net-worth snapshot for every user (one user failing never stops the rest)."""
    from app.models.user import User
    from app.services.insights_service import take_snapshot, today_dubai

    done = failed = 0
    async with ctx["session_factory"]() as session:
        user_ids = list((await session.execute(select(User.id).where(User.deleted_at.is_(None)))).scalars())
    today = today_dubai()
    for uid in user_ids:
        try:
            async with ctx["session_factory"]() as session:
                await take_snapshot(session, uid, today)
            done += 1
        except Exception:
            log.exception("net_worth_snapshot_failed", user_id=str(uid))
            failed += 1
    return {"snapshotted": done, "failed": failed}


async def refresh_valuations(ctx: dict[str, Any]) -> dict[str, Any]:
    """arq cron: refresh gold, FX and property-index quotes, then revalue automatically valued assets.
    Source failures keep the previous quotes; assets then fall back to their manual value once stale."""
    from app.services.valuations.providers import get_providers
    from app.services.valuations.service import refresh_all

    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.valuation_timeout_seconds)) as client:
        providers = ctx.get("valuation_providers") or get_providers(client)
        async with ctx["session_factory"]() as session:
            rep = await refresh_all(session, providers)
    log.info("valuations_refreshed", gold=rep.gold, fx=rep.fx, property_index=rep.property_index)
    return {
        "gold": rep.gold, "fx": rep.fx, "property_index": rep.property_index, "silver": rep.silver,
        "crypto": rep.crypto, "stocks": rep.stocks, "funds": rep.funds,
        "assets_revalued": rep.assets_revalued, "assets_unvalued": rep.assets_unvalued, "errors": rep.errors,
    }  # fmt: skip

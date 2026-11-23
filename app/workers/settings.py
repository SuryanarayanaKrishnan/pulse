from typing import Any

from arq import cron

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.logging import configure_logging
from app.core.queue import redis_settings
from app.services.storage import get_storage
from app.workers.tasks import (
    process_ingest,
    process_statement,
    sync_all_gmail_connections,
    sync_gmail_connection,
)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    ctx["session_factory"] = get_sessionmaker()
    ctx["storage"] = get_storage()


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()


class WorkerSettings:
    """Run with: arq app.workers.settings.WorkerSettings"""

    functions = [process_ingest, process_statement, sync_gmail_connection]
    # Every 6 hours: fan out one sync job per active Gmail connection.
    cron_jobs = [cron(sync_all_gmail_connections, hour={0, 6, 12, 18}, minute={5}, run_at_startup=False)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_tries = 3
    job_timeout = 120
    keep_result = 3600

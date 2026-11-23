from typing import Any

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.logging import configure_logging
from app.core.queue import redis_settings
from app.workers.tasks import process_ingest, process_statement


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    ctx["session_factory"] = get_sessionmaker()


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()


class WorkerSettings:
    """Run with: arq app.workers.settings.WorkerSettings"""

    functions = [process_ingest, process_statement]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_tries = 3
    job_timeout = 120
    keep_result = 3600

import asyncio
from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.cloudtasks import CloudTasksQueue
from app.core.config import get_settings


class QueuePool(Protocol):
    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any: ...
    async def ping(self) -> Any: ...


_pool: ArqRedis | CloudTasksQueue | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_queue() -> QueuePool:
    global _pool
    if _pool is None:
        settings = get_settings()
        if settings.queue_backend == "cloudtasks":
            _pool = CloudTasksQueue(settings)
        else:
            _pool = await create_pool(redis_settings())
    return _pool  # type: ignore[return-value]


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def get_queue_or_none() -> QueuePool | None:
    """For readiness probes: a dead Redis must degrade /ready, not raise."""
    try:
        return await asyncio.wait_for(get_queue(), timeout=3)
    except Exception:
        return None

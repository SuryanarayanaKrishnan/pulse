from collections.abc import Callable

from httpx import AsyncClient


async def test_internal_hidden_when_backend_is_arq(make_client: Callable[..., AsyncClient]) -> None:
    client = make_client()
    assert (await client.post("/internal/cron/refresh_valuations")).status_code == 404
    assert (await client.post("/internal/tasks/process_ingest", json={"args": ["x"]})).status_code == 404

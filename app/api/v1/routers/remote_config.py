from typing import Any

from fastapi import APIRouter, Request, Response

from app.core.config import get_settings
from app.services.remote_config import cached_config, derive_seed, signed

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(request: Request, response: Response) -> Any:
    """Signed feature flags and parameters. Public and identity-free: rollouts are decided on the phone."""
    payload = cached_config()
    etag = f'"{payload["version"]}"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=300"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    st = get_settings()
    seed = st.config_signing_key or (derive_seed(st.fernet_keys[0]) if st.fernet_keys else "")
    return signed(payload, seed)

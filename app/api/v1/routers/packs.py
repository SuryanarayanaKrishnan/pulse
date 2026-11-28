from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.core.config import get_settings
from app.services.packs import load_controls, load_packs, manifest
from app.services.remote_config import derive_seed, signed

router = APIRouter(prefix="/packs", tags=["packs"])


def _seed() -> str:
    st = get_settings()
    return st.config_signing_key or (derive_seed(st.fernet_keys[0]) if st.fernet_keys else "")


@router.get("")
async def pack_manifest(response: Response) -> dict[str, Any]:
    """Signed list of packs with versions and hashes. Public and identity-free."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return signed(manifest(load_packs(), load_controls()), _seed())


@router.get("/{name}")
async def get_pack(name: str, response: Response) -> dict[str, Any]:
    pack = load_packs().get(name)
    if pack is None:
        raise HTTPException(status_code=404, detail="unknown pack")
    response.headers["Cache-Control"] = "public, max-age=300"
    return signed(pack, _seed())

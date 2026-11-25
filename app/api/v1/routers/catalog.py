from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Response

from app.services.card_catalog import load_catalog, public_catalog

router = APIRouter(prefix="/catalog", tags=["catalog"])


@lru_cache(maxsize=1)
def _document() -> dict[str, Any]:
    return public_catalog(load_catalog())


@router.get("/cards")
async def catalog_cards(response: Response) -> dict[str, Any]:
    """The versioned card catalog for the app to cache. Public reference data: no user data goes in or out."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return _document()

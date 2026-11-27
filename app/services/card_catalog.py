"""Load the versioned UAE card catalog (data/cards/catalog.json) into ``cards_catalog``.

Pure row-building (``catalog_rows``) is shared by the Alembic data migration and the re-sync CLI
(``python -m app.services.card_catalog``). Upserts key on (issuer, name); rows are only ever added or
updated, and a card that disappears from the file is deleted only when no user holds it.

Mapping notes
- ``verified_at`` is set only for ``confidence == "high"`` cards (cross-checked on an official page),
  so the API's ``data_status`` stays honest: everything else is ``unverified`` but still has a source.
- Everything the engine does not read lives under ``perks`` (fees, waiver, welcome offer, deals, status,
  confidence, notes) so the phone can show it without schema changes.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "data" / "cards" / "catalog.json"
OFFICIAL = {"bank_page", "kfs", "tc_pdf"}
PERK_KEYS = (
    "art",
    "tier",
    "kind",
    "islamic",
    "status",
    "joining_fee_minor",
    "fee_waiver",
    "fx_markup_pct",
    "welcome_offer",
    "deals",
    "notes",
    "confidence",
    "community_notes",
    "field_confidence",
)

UPSERT = sa.text(
    "INSERT INTO cards_catalog (issuer, name, network, annual_fee_minor, min_salary_minor, "
    "reward_structure, perks, source_url, verified_at) VALUES (:issuer, :name, :network, :fee, :salary, "
    "CAST(:rs AS jsonb), CAST(:perks AS jsonb), :source_url, :verified_at) "
    "ON CONFLICT (issuer, name) DO UPDATE SET network = EXCLUDED.network, "
    "annual_fee_minor = EXCLUDED.annual_fee_minor, min_salary_minor = EXCLUDED.min_salary_minor, "
    "reward_structure = EXCLUDED.reward_structure, perks = EXCLUDED.perks, "
    "source_url = EXCLUDED.source_url, verified_at = EXCLUDED.verified_at, updated_at = now()"
)
# Rows with these perks were never synced from the catalog: the old illustrative seeds.
DELETE_OLD_SEEDS = sa.text(
    "DELETE FROM cards_catalog WHERE perks = '{}'::jsonb AND verified_at IS NULL "
    "AND id NOT IN (SELECT catalog_card_id FROM user_cards)"
)
DELETE_RETIRED = sa.text(
    "DELETE FROM cards_catalog WHERE perks ->> 'catalog_id' IS NOT NULL "
    "AND NOT (perks ->> 'catalog_id' = ANY(:ids)) "
    "AND id NOT IN (SELECT catalog_card_id FROM user_cards)"
)


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    p = path or Path(os.environ.get("CARD_CATALOG_PATH", DEFAULT_PATH))
    data: dict[str, Any] = json.loads(p.read_text())
    return data


def _verified_at(card: dict[str, Any]) -> datetime | None:
    if card.get("confidence") != "high":
        return None
    days = [s["verified_on"] for s in card.get("sources", []) if s["kind"] in OFFICIAL]
    return datetime.fromisoformat(max(days)).replace(tzinfo=UTC) if days else None


def _source_url(card: dict[str, Any]) -> str | None:
    srcs = card.get("sources", [])
    best = next((s for s in srcs if s["kind"] in OFFICIAL), srcs[0] if srcs else None)
    return best["url"] if best else None


def catalog_rows(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for ``UPSERT``. Discontinued cards are left out."""
    rows = []
    for c in catalog["cards"]:
        if c.get("status") == "discontinued":
            continue
        perks: dict[str, Any] = {k: c[k] for k in PERK_KEYS if c.get(k) not in (None, [], {})}
        perks.update(
            catalog_id=c["id"],
            catalog_version=catalog["catalog_version"],
            annual_fee_known=c.get("annual_fee_minor") is not None,
            benefits=c.get("perks") or {},
        )
        rows.append(
            {
                "issuer": c["issuer"],
                "name": c["name"],
                "network": c.get("network"),
                "fee": c.get("annual_fee_minor") or 0,
                "salary": c.get("min_salary_minor"),
                "rs": json.dumps(c["reward_structure"]),
                "perks": json.dumps(perks),
                "source_url": _source_url(c),
                "verified_at": _verified_at(c),
            }
        )
    return rows


def public_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """The catalog in the shape the phone downloads (``GET /v1/catalog/cards``).

    ``version`` is the catalog date as an integer (2026-11-24 -> 20261124) so the app can compare versions
    numerically. Discontinued cards are left out. Everything the phone shows beyond the reward rules
    (fees, waiver, welcome offer, notes, confidence) sits under ``perks``, as in ``catalog_rows``.
    """
    cards = []
    for c in catalog["cards"]:
        if c.get("status") == "discontinued":
            continue
        perks: dict[str, Any] = {k: c[k] for k in PERK_KEYS if c.get(k) not in (None, [], {})}
        perks["benefits"] = c.get("perks") or {}
        verified = _verified_at(c)
        cards.append(
            {
                "id": c["id"],
                "issuer": c["issuer"],
                "name": c["name"],
                "network": c.get("network"),
                "annual_fee_minor": c.get("annual_fee_minor") or 0,
                "min_salary_minor": c.get("min_salary_minor"),
                "reward_structure": c["reward_structure"],
                "perks": perks,
                "art": c.get("art"),
                "verified_on": verified.date().isoformat() if verified else None,
                "source": _source_url(c),
            }
        )
    day = str(catalog["catalog_version"])
    return {
        "version": int(day.replace("-", "")),
        "updated_on": day,
        "cards": cards,
    }


def sync_catalog_conn(bind: Any, catalog: dict[str, Any], *, retire: bool = True) -> int:
    """Synchronous upsert on an Alembic/SQLAlchemy connection. Returns the number of cards written."""
    rows = catalog_rows(catalog)
    bind.execute(DELETE_OLD_SEEDS)
    for r in rows:
        bind.execute(UPSERT, r)
    if retire:
        ids = [c["id"] for c in catalog["cards"] if c.get("status") != "discontinued"]
        bind.execute(DELETE_RETIRED, {"ids": ids})
    return len(rows)


async def _main() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    catalog = load_catalog()
    async with engine.begin() as conn:
        n = await conn.run_sync(lambda c: sync_catalog_conn(c, catalog))
    await engine.dispose()
    print(f"synced {n} cards, catalog_version {catalog['catalog_version']}")


if __name__ == "__main__":
    asyncio.run(_main())

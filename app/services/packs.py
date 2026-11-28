"""Versioned content packs the app downloads instead of shipping in a build.

Examples: tips, walkthrough text, parser rules. Each ``data/packs/<name>.json`` is an object with an integer
``version``. The manifest lists every pack with its version and sha256 so the phone fetches only what changed.
Packs carry reference content only: nothing about users.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.remote_config import canonical

PACK_DIR = Path(__file__).resolve().parents[2] / "data" / "packs"
CONTROL_FILE = "_control.json"
NAME = re.compile(r"^[a-z0-9_]+$")


@lru_cache(maxsize=1)
def load_packs(directory: Path = PACK_DIR) -> dict[str, dict[str, Any]]:
    packs: dict[str, dict[str, Any]] = {}
    for f in sorted(directory.glob("*.json")):
        if f.name == CONTROL_FILE:
            continue
        data: dict[str, Any] = json.loads(f.read_text(encoding="utf-8"))
        if not NAME.match(f.stem) or not isinstance(data.get("version"), int):
            raise ValueError(f"bad pack {f.name}: needs a simple name and an integer version")
        packs[f.stem] = data
    return packs


def load_controls(directory: Path = PACK_DIR) -> dict[str, dict[str, Any]]:
    """Per-pack rollout controls from ``_control.json`` (all optional).

    - ``rollout_pct`` 0..100: share of phones that take a new version (decided on the phone).
    - ``min_build``: oldest app build allowed to take the pack.
    - ``max_version``: rollback ceiling; a phone holding a newer version drops it and uses the bundled copy.
    - ``killed``: true makes every phone drop its downloaded copy and use the bundled one.
    """
    f = directory / CONTROL_FILE
    if not f.exists():
        return {}
    raw: dict[str, dict[str, Any]] = json.loads(f.read_text(encoding="utf-8")).get("packs", {})
    for name, c in raw.items():
        pct = c.get("rollout_pct", 100)
        if not isinstance(pct, int) or not 0 <= pct <= 100:
            raise ValueError(f"control {name}: rollout_pct must be 0..100")
        mv = c.get("max_version")
        if mv is not None and not isinstance(mv, int):
            raise ValueError(f"control {name}: max_version must be an integer or null")
    return raw


def manifest(
    packs: dict[str, dict[str, Any]], controls: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    controls = controls or {}
    out: dict[str, Any] = {}
    for n, p in packs.items():
        c = controls.get(n, {})
        out[n] = {
            "version": p["version"],
            "sha256": hashlib.sha256(canonical(p)).hexdigest(),
            "rollout_pct": c.get("rollout_pct", 100),
            "min_build": c.get("min_build", 0),
            "max_version": c.get("max_version"),
            "killed": bool(c.get("killed", False)),
        }
    return {"packs": out}

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
NAME = re.compile(r"^[a-z0-9_]+$")


@lru_cache(maxsize=1)
def load_packs(directory: Path = PACK_DIR) -> dict[str, dict[str, Any]]:
    packs: dict[str, dict[str, Any]] = {}
    for f in sorted(directory.glob("*.json")):
        data: dict[str, Any] = json.loads(f.read_text(encoding="utf-8"))
        if not NAME.match(f.stem) or not isinstance(data.get("version"), int):
            raise ValueError(f"bad pack {f.name}: needs a simple name and an integer version")
        packs[f.stem] = data
    return packs


def manifest(packs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "packs": {
            n: {"version": p["version"], "sha256": hashlib.sha256(canonical(p)).hexdigest()}
            for n, p in packs.items()
        }
    }

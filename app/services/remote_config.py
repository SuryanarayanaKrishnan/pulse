"""Remote config: feature flags and tunable parameters the app fetches instead of shipping in a build.

Privacy: the server never learns who the caller is. Percentage rollouts are decided on the phone by hashing a
local random id against the flag's ``rollout_pct``; the request carries nothing identifying.

The payload is signed with an Ed25519 key (``CONFIG_SIGNING_KEY``, base64 of the 32-byte seed) so a phone can
reject a tampered or spoofed config. With no key set (dev/test) ``signature`` is null.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "data" / "config" / "remote_config.json"


def load_config(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    validate(cfg)
    return cfg


def validate(cfg: dict[str, Any]) -> None:
    if not isinstance(cfg.get("version"), int):
        raise ValueError("config needs an integer version")
    for name, f in cfg.get("flags", {}).items():
        if not isinstance(f.get("enabled"), bool):
            raise ValueError(f"flag {name}: enabled must be a bool")
        pct = f.get("rollout_pct", 100)
        if not isinstance(pct, int) or not 0 <= pct <= 100:
            raise ValueError(f"flag {name}: rollout_pct must be 0..100")


def canonical(payload: dict[str, Any]) -> bytes:
    """The exact bytes that are signed; the app re-serialises the same way."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _key(seed_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed_b64))


def public_key_b64(seed_b64: str) -> str:
    raw = _key(seed_b64).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def signed(payload: dict[str, Any], seed_b64: str) -> dict[str, Any]:
    sig = base64.b64encode(_key(seed_b64).sign(canonical(payload))).decode() if seed_b64 else None
    return {"payload": payload, "signature": sig, "alg": "ed25519" if sig else None}


@lru_cache(maxsize=1)
def cached_config() -> dict[str, Any]:
    return load_config()

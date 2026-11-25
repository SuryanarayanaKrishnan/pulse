import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.services.remote_config import canonical, load_config, public_key_b64, signed, validate


async def test_config_is_public_with_etag(make_client):
    c = make_client()
    r = await c.get("/v1/config")
    assert r.status_code == 200
    body = r.json()
    assert body["payload"]["version"] > 20200101 and "flags" in body["payload"]
    again = await c.get("/v1/config", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_signature_verifies_and_detects_tampering():
    seed = base64.b64encode(b"\x01" * 32).decode()
    out = signed(load_config(), seed)
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64(seed)))
    pub.verify(base64.b64decode(out["signature"]), canonical(out["payload"]))
    out["payload"]["flags"]["nicheIntel"]["enabled"] = False
    with pytest.raises(InvalidSignature):
        pub.verify(base64.b64decode(out["signature"]), canonical(out["payload"]))


def test_unsigned_without_key():
    assert signed(load_config(), "")["signature"] is None


def test_bad_rollout_rejected():
    with pytest.raises(ValueError):
        validate({"version": 1, "flags": {"x": {"enabled": True, "rollout_pct": 150}}})

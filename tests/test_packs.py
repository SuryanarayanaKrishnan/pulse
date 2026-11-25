import pytest

from app.services.packs import PACK_DIR, load_packs, manifest


async def test_manifest_and_pack_are_public(make_client):
    c = make_client()
    m = (await c.get("/v1/packs")).json()["payload"]["packs"]
    assert {"tips", "walkthrough", "parser_rules"} <= m.keys()
    r = await c.get("/v1/packs/tips")
    assert r.status_code == 200 and r.json()["payload"]["version"] == m["tips"]["version"]
    assert (await c.get("/v1/packs/nope")).status_code == 404
    assert (await c.get("/v1/packs/..%2Fx")).status_code in (404, 422)


def test_manifest_hash_changes_with_content():
    packs = load_packs(PACK_DIR)
    changed = {**packs, "tips": {**packs["tips"], "items": []}}
    assert manifest(packs)["packs"]["tips"]["sha256"] != manifest(changed)["packs"]["tips"]["sha256"]


def test_bad_pack_rejected(tmp_path):
    (tmp_path / "x.json").write_text('{"no_version": 1}')
    with pytest.raises(ValueError):
        load_packs.__wrapped__(tmp_path)

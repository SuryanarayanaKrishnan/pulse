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


def test_manifest_carries_rollout_controls():
    packs = load_packs(PACK_DIR)
    m = manifest(packs, {"tips": {"rollout_pct": 20, "max_version": 3, "killed": True}})["packs"]
    assert m["tips"]["rollout_pct"] == 20 and m["tips"]["max_version"] == 3 and m["tips"]["killed"] is True
    assert m["walkthrough"]["rollout_pct"] == 100 and m["walkthrough"]["killed"] is False


def test_bad_control_rejected(tmp_path):
    from app.services.packs import load_controls

    (tmp_path / "_control.json").write_text('{"packs": {"tips": {"rollout_pct": 500}}}')
    with pytest.raises(ValueError):
        load_controls(tmp_path)


def test_shipped_controls_are_valid_and_name_real_packs():
    from app.services.packs import load_controls

    assert set(load_controls()) <= set(load_packs(PACK_DIR))

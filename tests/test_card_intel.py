import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import reddit_intel as ri  # noqa: E402

from app.services import card_intel as ci  # noqa: E402

CATALOG = json.loads((ROOT / "data" / "cards" / "catalog.json").read_text())
IDS = {c["id"] for c in CATALOG["cards"]}


def test_shipped_intel_is_valid_and_ids_exist():
    assert ci.validate(ci.load_intel(), IDS) == []


def _intel(**over):
    item = {
        "id": "x",
        "kind": "caveat",
        "title": "t",
        "body": "b",
        "card_ids": ["a", "b"],
        "severity": "warn",
        "confidence": "reported",
        "status": "active",
        "sources": [{"kind": "user_report", "ref": "r"}],
    } | over
    return {"items": [item]}


def test_matches_only_owned_cards_and_dates():
    i = _intel(valid_until="2026-12-01")
    assert ci.intel_for_cards(i, {"a"}, date(2026, 11, 30))
    assert not ci.intel_for_cards(i, {"z"}, date(2026, 11, 30))
    assert not ci.intel_for_cards(i, {"a"}, date(2026, 12, 2))


def test_combo_suggests_missing_card_but_caveat_does_not():
    combo = ci.intel_for_cards(_intel(kind="combo"), {"a"}, date(2026, 11, 30))[0]
    assert combo.missing_card_ids == ["b"]
    cav = ci.intel_for_cards(_intel(), {"a"}, date(2026, 11, 30))[0]
    assert cav.missing_card_ids == []


def test_recommender_trust_and_validation():
    i = _intel(effect={"type": "rate_override", "card_id": "a", "rate": 0.005})
    adj = ci.recommender_adjustments(i, date(2026, 11, 30))
    assert adj[0]["trust"] == 0.5 and adj[0]["rate"] == 0.005
    assert ci.validate(_intel(effect={"type": "rate_override", "rate": 0.9})) != []
    assert ci.validate(_intel(sources=[])) != []


def test_reddit_candidates_need_card_and_issue():
    idx = ri.card_index(CATALOG)
    posts = [
        {
            "id": "a1",
            "title": "SIB Tabby only works via Apple Pay",
            "selftext": "",
            "permalink": "/r/x/a1",
            "created_utc": 1795000000,
            "score": 40,
            "num_comments": 9,
        },
        {
            "id": "a2",
            "title": "Which laptop?",
            "selftext": "",
            "permalink": "/r/x/a2",
            "created_utc": 1795000000,
            "score": 40,
        },
        {
            "id": "a3",
            "title": "SIB Tabby only works via Apple Pay",
            "selftext": "",
            "permalink": "/r/x/a3",
            "created_utc": 1795000000,
            "score": 1,
        },
    ]
    got = ri.candidates(posts, idx)
    assert [c["post_id"] for c in got] == ["a1"]
    assert "sib-cashback" in got[0]["card_ids"] and "caveat" in got[0]["suggested_kinds"]


def test_starter_items_need_no_cards_and_free_comes_first():
    intel = ci.load_intel()
    items = ci.starter_items(intel, date(2026, 11, 30))
    tiers = [r.item.get("tier", ci.DEFAULT_TIER) for r in items]
    assert "free" in tiers and "plus" in tiers
    assert tiers == sorted(tiers, key=lambda t: t != "free")  # free block, then plus
    assert all(r.owned_card_ids == [] for r in items)
    combo = next(r for r in items if r.item["kind"] == "combo")
    assert combo.missing_card_ids == combo.item["card_ids"]
    assert combo.out()["tier"] in ci.TIERS


def test_starter_items_respect_dates_and_missing_tier_defaults_to_plus():
    i = _intel(kind="combo", valid_until="2026-12-01")
    assert ci.starter_items(i, date(2026, 11, 30))[0].out()["tier"] == "plus"
    assert ci.starter_items(i, date(2026, 12, 2)) == []
    assert ci.validate(_intel(tier="gold")) != []
    assert ci.validate(_intel(tier="free")) == []


def test_shipped_free_combos_exist_for_new_users():
    free = [
        r
        for r in ci.starter_items(ci.load_intel(), date(2026, 11, 30))
        if r.item.get("tier") == "free" and r.item["kind"] == "combo"
    ]
    assert len(free) >= 3

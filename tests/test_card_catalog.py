import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_card_catalog as b  # noqa: E402


def test_banks_validate():
    catalog, errs = b.build()
    assert errs == []
    assert catalog["count"] >= 100


def test_catalog_json_is_current():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_card_catalog.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout


def test_no_unrealistic_earn_rates():
    cat = json.loads((ROOT / "data" / "cards" / "catalog.json").read_text())
    for c in cat["cards"]:
        rs = c["reward_structure"]
        if rs.get("unit") == "cashback":
            for r in rs.get("rules", []):
                assert r["rate"] < 0.5, (c["id"], r["name"])


def test_catalog_rows_map_verification_honestly():
    from app.services.card_catalog import catalog_rows, load_catalog

    cat = load_catalog()
    rows = catalog_rows(cat)
    by_conf = {c["id"]: c for c in cat["cards"]}
    assert len(rows) == len([c for c in cat["cards"] if c["status"] != "discontinued"])
    for r in rows:
        perks = json.loads(r["perks"])
        assert (r["verified_at"] is not None) == (by_conf[perks["catalog_id"]]["confidence"] == "high")
        assert r["source_url"].startswith("https://")

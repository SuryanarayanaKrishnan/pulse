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
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_card_catalog.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_no_unrealistic_earn_rates():
    cat = json.loads((ROOT / "data" / "cards" / "catalog.json").read_text())
    for c in cat["cards"]:
        rs = c["reward_structure"]
        if rs.get("unit") == "cashback":
            for r in rs.get("rules", []):
                assert r["rate"] < 0.5, (c["id"], r["name"])

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


def test_every_card_has_readable_art():
    from app.services.card_art import MIN_CONTRAST, contrast, validate_art

    cat = json.loads((ROOT / "data" / "cards" / "catalog.json").read_text())
    for c in cat["cards"]:
        assert validate_art(c.get("art"), c) == [], c["id"]
        for s in c["art"]["stops"]:
            assert contrast(c["art"]["text"], s) >= MIN_CONTRAST, (c["id"], s)


def test_generated_art_is_deterministic_and_maps_to_perks():
    from app.services.card_art import generate
    from app.services.card_catalog import catalog_rows, load_catalog

    cat = load_catalog()
    for c in cat["cards"][:20]:
        plain = {k: v for k, v in c.items() if k != "art"}
        assert generate(plain) == generate(plain)
    for r in catalog_rows(cat):
        assert "art" in json.loads(r["perks"])


def test_no_comparison_site_passes_as_official_source():
    import json
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    import build_card_catalog as b

    card = {
        "id": "x-y",
        "issuer": "X",
        "name": "Y",
        "network": None,
        "status": "active",
        "confidence": "medium",
        "reward_structure": {"unit": "cashback", "base_rate": 0.01, "rules": []},
        "sources": [{"url": "https://kredit.ae/x", "kind": "bank_page", "verified_on": "2026-11-27"}],
        "field_confidence": {"annual_fee_minor": "maybe"},
    }
    errs = b.validate(card, "X")
    assert any("not an official source" in e for e in errs)
    assert any("field_confidence" in e for e in errs)
    assert b.build()[1] == [], json.dumps(b.build()[1])


def test_evidence_report_is_current():
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    import card_evidence_report as r

    assert (root / "docs" / "CARD_EVIDENCE.md").read_text() == r.render(), (
        "run python scripts/card_evidence_report.py"
    )


REQUIRED_ISSUERS = {
    "HSBC",
    "Citibank",
    "Standard Chartered",
    "RAKBANK",
    "CBD",
    "Emirates NBD",
    "ADCB",
    "First Abu Dhabi Bank",
    "Mashreq",
    "Dubai Islamic Bank",
    "ADIB",
    "Emirates Islamic",
    "SIB",
    "NBF",
    "Dubai First",
    "Liv",
    "Wio",
    "Tabby",
    "American Express",
}


def test_every_major_uae_issuer_has_a_live_card_in_the_catalog():
    import json
    from pathlib import Path

    cards = json.loads((Path(__file__).resolve().parent.parent / "data/cards/catalog.json").read_text())[
        "cards"
    ]
    live = {c["issuer"] for c in cards if c["status"] != "discontinued"}
    assert REQUIRED_ISSUERS <= live, f"missing issuers: {sorted(REQUIRED_ISSUERS - live)}"

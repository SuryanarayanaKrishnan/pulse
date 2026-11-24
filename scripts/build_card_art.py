#!/usr/bin/env python3
"""Generate data/cards/art.json (id -> face art) for every card in the bank files.

Usage: python scripts/build_card_art.py [--force]    (default keeps hand-edited entries and only fills gaps)
       python scripts/build_card_art.py --review     (also rewrite docs/card-art-review.md)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.card_art import OVERRIDES, generate, infer_tier  # noqa: E402

BANKS = ROOT / "data" / "cards" / "banks"
OUT = ROOT / "data" / "cards" / "art.json"


def all_cards() -> list[dict]:
    cards: list[dict] = []
    for f in sorted(BANKS.glob("*.json")):
        cards += json.loads(f.read_text())["cards"]
    return sorted(cards, key=lambda c: c["id"])


def main() -> int:
    cards = all_cards()
    old = json.loads(OUT.read_text()) if OUT.exists() and "--force" not in sys.argv else {}
    art = {c["id"]: old.get(c["id"]) or generate(c) for c in cards}
    OUT.write_text(json.dumps(art, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote art for {len(art)} cards ({len(OVERRIDES)} hand overrides)")
    if "--review" in sys.argv:
        lines = [
            "# Card art review",
            "",
            "Generated faces; `bank palette + tier finish + kind motif`. Rows marked `override` were set by hand from",
            "memory of the real product; the rest are bank-palette guesses. Colours are approximate.",
            "A network guess means the catalog has no network for the card.",
            "",
            "| id | bank | name | tier | guess | basis |",
            "|---|---|---|---|---|---|",
        ]
        for c in cards:
            a = art[c["id"]]
            guess = f"{a['finish']}, {a['stops'][0]}→{a['stops'][1]}, accent {a['accent']}, {a['motif']}, {a['network']}"
            basis = "override" if c["id"] in OVERRIDES else "bank palette"
            if c.get("network") is None:
                basis += ", network guessed"
            if c.get("tier") == "other":
                basis += f", tier from name ({infer_tier(c)})"
            lines.append(f"| {c['id']} | {c['issuer']} | {c['name']} | {a['tier']} | {guess} | {basis} |")
        (ROOT / "docs").mkdir(exist_ok=True)
        Path(ROOT / "docs" / "card-art-review.md").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

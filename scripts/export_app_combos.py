"""Write the default combos bundle the app ships (pulse-app/assets/catalog/combos.json).

Usage: python scripts/export_app_combos.py ../pulse-app/assets/catalog/combos.json

Same shape as GET /v1/cards/combos, minus anything per-user, so the app works offline with no cards.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import card_intel  # noqa: E402


def main(out: str) -> None:
    intel = card_intel.load_intel()
    # 'watching' items are unproven; they never ship to the app's default list.
    items = [
        r.out() for r in card_intel.starter_items(intel, date.today()) if r.item.get("status") != "watching"
    ]
    Path(out).write_text(
        json.dumps(
            {
                "generated_by": "pulse scripts/export_app_combos.py: do not edit by hand",
                "as_of": intel["as_of"],
                "items": items,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"{len(items)} items -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "combos.json")

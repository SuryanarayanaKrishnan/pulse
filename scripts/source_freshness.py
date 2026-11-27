#!/usr/bin/env python3
"""Flag card data that nobody has re-checked recently (stdlib only, free on GitHub Actions).

offer_watch.py notices when an official page *changes*. This notices the opposite failure: data that
has quietly gone stale because nobody looked. It prints a markdown report and exits 0; the workflow
opens (or refreshes) one issue when the report is not empty.

Flags:
  - a claim (data/cards/claims.json) last checked more than CLAIM_DAYS ago;
  - a card source last verified more than SOURCE_DAYS ago, for high/medium confidence cards;
  - an intel item or combo past its review_by date;
  - an official source that the watcher could not read on its last runs (data/cards/offers_feed.json).

Usage: python scripts/source_freshness.py [--today YYYY-MM-DD]
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data" / "cards"
CLAIM_DAYS = 60
SOURCE_DAYS = 120


def report(today: date) -> list[str]:
    out: list[str] = []
    catalog = json.loads((D / "catalog.json").read_text())
    claims = json.loads((D / "claims.json").read_text())["claims"]
    intel = json.loads((D / "intel.json").read_text())["items"]
    for c in claims:
        age = (today - date.fromisoformat(c["checked_on"])).days
        if age > CLAIM_DAYS:
            out.append(
                f"- claim `{c['id']}` last checked {c['checked_on']} ({age} days): re-read {c['source_url']}"
            )
    for card in catalog["cards"]:
        if card.get("status") == "discontinued" or card.get("confidence") == "low":
            continue
        for s in card["sources"]:
            if s["kind"] in {"bank_page", "kfs", "tc_pdf"}:
                age = (today - date.fromisoformat(s["verified_on"])).days
                if age > SOURCE_DAYS:
                    out.append(
                        f"- card `{card['id']}` official source verified {s['verified_on']} ({age} days): {s['url']}"
                    )
    for it in intel:
        rb = it.get("review_by")
        if rb and it.get("status") != "retired" and date.fromisoformat(rb) < today:
            out.append(f"- intel `{it['id']}` was due for review by {rb}")
    feed = D / "offers_feed.json"
    if feed.exists():
        for e in json.loads(feed.read_text()).get("errors", []):
            out.append(
                f"- watcher could not read {e['url']}: {e['error']} (is the page gone, or blocking us?)"
            )
    return out


def main(argv: list[str]) -> int:
    today = date.fromisoformat(argv[argv.index("--today") + 1]) if "--today" in argv else date.today()
    lines = report(today)
    if lines:
        print(f"## Card data needs a re-check ({today})\n")
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

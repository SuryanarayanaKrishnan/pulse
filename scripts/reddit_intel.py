#!/usr/bin/env python3
"""Turn raw r/UAECreditCards posts into *candidate* intel for human review (stdlib only, no LLM).

Input: a JSON list of posts ({id, title, selftext, permalink, created_utc, score, num_comments}) saved by
whatever fetcher is in use (the project's Reddit connector now; a company account later). Output:
data/cards/intel_candidates.json, which a human promotes into data/cards/intel.json. Nothing here is
published to users automatically.

Usage: python scripts/reddit_intel.py --input posts.json [--min-score 3]
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "cards" / "catalog.json"
OUT = ROOT / "data" / "cards" / "intel_candidates.json"

KIND_PATTERNS = {
    "caveat": r"not (getting|receiving|earning)|no cashback|didn.?t (get|receive)|0\.5%|mcc|excluded|stopped|only (works|via)|devalu|capped|not working|reduced",
    "bonus": r"welcome (bonus|offer|gift)|sign.?up (bonus|offer)|joining (bonus|gift)|referral|free for life|fee waiver",
    "combo": r"stack|combo|pair(ed|ing)? with|use .{2,40} (for|with) .{2,40}(and|\+)|best (card|combo)",
    "offer_change": r"ending|ends on|valid (until|till)|new offer|changes? (from|on)|from \d{1,2} (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
}
ALIASES = {"tabby": "tabby-card", "sib": "sib-cashback", "sharjah islamic": "sib-cashback"}


def card_index(catalog: dict) -> dict[str, str]:
    idx = dict(ALIASES)
    for c in catalog["cards"]:
        idx[f"{c['issuer']} {c['name']}".lower()] = c["id"]
        idx[c["name"].lower()] = c["id"]
    return idx


def mentioned_cards(text: str, idx: dict[str, str]) -> list[str]:
    low = text.lower()
    found = {
        cid for alias, cid in idx.items() if len(alias) >= 3 and re.search(rf"\b{re.escape(alias)}\b", low)
    }
    return sorted(found)


def candidates(posts: list[dict], idx: dict[str, str], min_score: int = 3) -> list[dict]:
    out = []
    for p in posts:
        if p.get("score", 0) < min_score:
            continue
        text = f"{p.get('title', '')}\n{p.get('selftext', '')}"
        kinds = [k for k, rx in KIND_PATTERNS.items() if re.search(rx, text, re.I)]
        cards = mentioned_cards(text, idx)
        if not kinds or not cards:
            continue
        out.append(
            {
                "post_id": p["id"],
                "url": "https://www.reddit.com" + p.get("permalink", ""),
                "title": p.get("title", ""),
                "posted": datetime.fromtimestamp(p.get("created_utc", 0), UTC).date().isoformat(),
                "score": p.get("score", 0),
                "comments": p.get("num_comments", 0),
                "suggested_kinds": kinds,
                "card_ids": cards,
                "status": "needs_review",
            }
        )
    out.sort(key=lambda c: (-c["score"], c["post_id"]))
    return out


def main(argv: list[str]) -> int:
    if "--input" not in argv:
        print(__doc__)
        return 2
    posts = json.loads(Path(argv[argv.index("--input") + 1]).read_text())
    min_score = int(argv[argv.index("--min-score") + 1]) if "--min-score" in argv else 3
    idx = card_index(json.loads(CATALOG.read_text()))
    cands = candidates(posts, idx, min_score)
    OUT.write_text(
        json.dumps({"generated": datetime.now(UTC).date().isoformat(), "candidates": cands}, indent=1) + "\n"
    )
    print(f"{len(cands)} candidates from {len(posts)} posts -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

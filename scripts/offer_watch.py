#!/usr/bin/env python3
"""Offer/terms change watcher for the UAE card catalog (stdlib only, free to run on GitHub Actions).

For every official source URL (bank_page / kfs / tc_pdf) in data/cards/catalog.json it:
  1. fetches the page politely (robots.txt respected, identifying User-Agent, >= 3 s between hits per host),
  2. reduces it to "signal lines" (fees, cashback/points rates, welcome bonus, validity dates),
  3. diffs them against the stored snapshot in data/cards/snapshots/,
  4. writes data/cards/offers_feed.json (changes + offers expiring soon) for the app's "new offer" alerts,
  5. exits 0 always (a changed page is information, not a failure); prints a markdown summary.

Pages that are JavaScript-rendered return little static text; they are flagged `thin` instead of
producing false "removed" diffs. A human (or the research thread) then re-verifies the card and
updates data/cards/banks/*.json - the watcher never edits the catalog itself.

Usage: python scripts/offer_watch.py [--limit N] [--offline-dir DIR]
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "cards" / "catalog.json"
SNAP_DIR = ROOT / "data" / "cards" / "snapshots"
FEED = ROOT / "data" / "cards" / "offers_feed.json"
UA = "PulseOfferWatch/1.0 (+https://github.com/SuryanarayanaKrishnan/pulse; low-rate, robots.txt respected)"
OFFICIAL = {"bank_page", "kfs", "tc_pdf"}
MIN_GAP_S = 3.0
THIN_CHARS = 400
EXPIRY_WINDOW_DAYS = 14

SIGNAL = re.compile(
    r"(annual fee|joining fee|welcome|bonus|cash ?back|reward|miles|points|%|aed\s?[\d,]+|waive|"
    r"free for|valid (until|till|through)|offer ends|until \d|expires?|minimum (salary|income)|lounge|complimentary)",
    re.I,
)
DATE_PATTERNS = [
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(20\d\d)", re.I), "dmy"),
    (re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)", re.I), "mdy"),
    (re.compile(r"(20\d\d)-(\d\d)-(\d\d)"), "iso"),
]
MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag in ("p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):  # noqa: ANN001
        if not self._skip:
            self.parts.append(data)


def signal_lines(raw_html: str) -> list[str]:
    p = _Text()
    p.feed(raw_html)
    text = html.unescape("".join(p.parts))
    out: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if 6 <= len(line) <= 400 and SIGNAL.search(line) and line not in out:
            out.append(line)
    return out


def parse_dates(line: str) -> list[date]:
    found: list[date] = []
    for rx, kind in DATE_PATTERNS:
        for m in rx.finditer(line):
            try:
                if kind == "dmy":
                    found.append(date(int(m[3]), MONTHS[m[2][:3].lower()], int(m[1])))
                elif kind == "mdy":
                    found.append(date(int(m[3]), MONTHS[m[1][:3].lower()], int(m[2])))
                else:
                    found.append(date(int(m[1]), int(m[2]), int(m[3])))
            except ValueError:
                continue
    return found


def expiring(lines: list[str], today: date, window: int = EXPIRY_WINDOW_DAYS) -> list[dict]:
    hits = []
    for ln in lines:
        if not re.search(r"valid|until|till|ends?|expires?|offer period|promotion", ln, re.I):
            continue
        for d in parse_dates(ln):
            if today <= d <= today + timedelta(days=window):
                hits.append({"line": ln, "date": d.isoformat(), "days_left": (d - today).days})
    return hits


def diff_lines(old: list[str], new: list[str]) -> dict[str, list[str]]:
    so, sn = set(old), set(new)
    return {"added": [x for x in new if x not in so], "removed": [x for x in old if x not in sn]}


def robots_ok(url: str, cache: dict[str, urllib.robotparser.RobotFileParser]) -> bool:
    u = urllib.parse.urlsplit(url)
    base = f"{u.scheme}://{u.netloc}"
    rp = cache.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            rp.read()
        except Exception:  # noqa: BLE001  unreachable robots -> be conservative only on explicit disallow
            pass
        cache[base] = rp
    try:
        return rp.can_fetch(UA, url)
    except Exception:  # noqa: BLE001
        return True


def fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/pdf;q=0.5"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  https only (catalog validated)
        body = r.read(3_000_000)
        ctype = r.headers.get("Content-Type", "")
    if "pdf" in ctype.lower() or url.lower().endswith(".pdf"):
        return r.status, ""  # PDFs: tracked by ETag/size only in a future pass
    return r.status, body.decode("utf-8", "replace")


def snap_path(url: str) -> Path:
    return SNAP_DIR / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def watch_targets(catalog: dict) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    for c in catalog["cards"]:
        if c.get("status") in ("discontinued",):
            continue
        for s in c.get("sources", []):
            if s["kind"] in OFFICIAL and not s["url"].lower().endswith(".pdf"):
                targets.setdefault(s["url"], []).append(c["id"])
    return targets


def catalog_expiries(catalog: dict, today: date) -> list[dict]:
    out = []
    for c in catalog["cards"]:
        vu = (c.get("welcome_offer") or {}).get("valid_until")
        if vu:
            try:
                d = date.fromisoformat(vu)
            except ValueError:
                continue
            if today <= d <= today + timedelta(days=EXPIRY_WINDOW_DAYS):
                out.append({"card_id": c["id"], "valid_until": vu, "days_left": (d - today).days})
    return out


def main(argv: list[str]) -> int:
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    offline = Path(argv[argv.index("--offline-dir") + 1]) if "--offline-dir" in argv else None
    catalog = json.loads(CATALOG.read_text())
    today = datetime.now(timezone.utc).date()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    targets = list(watch_targets(catalog).items())[:limit]
    robots: dict[str, urllib.robotparser.RobotFileParser] = {}
    last_hit: dict[str, float] = {}
    changes, errors, thin = [], [], []

    for url, card_ids in targets:
        host = urllib.parse.urlsplit(url).netloc
        try:
            if offline:
                raw = (offline / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")).read_text()
            else:
                if not robots_ok(url, robots):
                    errors.append({"url": url, "error": "disallowed by robots.txt"})
                    continue
                wait = MIN_GAP_S - (time.monotonic() - last_hit.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                status, raw = fetch(url)
                last_hit[host] = time.monotonic()
                if status != 200:
                    errors.append({"url": url, "error": f"http {status}"})
                    continue
            lines = signal_lines(raw)
        except Exception as e:  # noqa: BLE001
            errors.append({"url": url, "error": str(e)[:200]})
            continue
        sp = snap_path(url)
        old = json.loads(sp.read_text()) if sp.exists() else None
        if len(raw) < THIN_CHARS or not lines:
            thin.append(url)
            continue
        if old is not None:
            d = diff_lines(old["lines"], lines)
            if d["added"] or d["removed"]:
                changes.append({"url": url, "card_ids": card_ids, "detected_on": today.isoformat(), **d})
        sp.write_text(json.dumps({"url": url, "fetched_on": today.isoformat(), "lines": lines}, indent=1, ensure_ascii=False))
        for ex in expiring(lines, today):
            changes.append({"url": url, "card_ids": card_ids, "detected_on": today.isoformat(), "expiring": ex})

    feed = {
        "generated_on": today.isoformat(),
        "changes": changes,
        "catalog_welcome_offers_expiring": catalog_expiries(catalog, today),
        "thin_pages": thin,
        "errors": errors,
    }
    FEED.write_text(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")
    print(f"## Offer watch {today}\n- pages checked: {len(targets)}\n- changes: {len(changes)}\n- thin/JS pages: {len(thin)}\n- errors: {len(errors)}")
    for c in changes[:40]:
        print(f"\n### {', '.join(c['card_ids'])}\n{c['url']}")
        for x in c.get("added", [])[:6]:
            print(f"+ {x}")
        for x in c.get("removed", [])[:6]:
            print(f"- {x}")
        if "expiring" in c:
            print(f"! expiring in {c['expiring']['days_left']}d: {c['expiring']['line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

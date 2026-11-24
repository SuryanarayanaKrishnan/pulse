#!/usr/bin/env python3
"""Validate data/cards/banks/*.json and merge into data/cards/catalog.json.

Usage: python scripts/build_card_catalog.py [--validate | --check]   (--validate: no write; --check fails if catalog.json is stale)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.card_art import validate_art  # noqa: E402
from app.services.rewards import parse_structure  # noqa: E402

BANKS = ROOT / "data" / "cards" / "banks"
OUT = ROOT / "data" / "cards" / "catalog.json"
ART = ROOT / "data" / "cards" / "art.json"
NETWORKS = {None, "visa", "mastercard", "amex", "unionpay"}
STATUS = {"active", "closed_to_new", "discontinued", "unverified"}
CONF = {"high", "medium", "low"}
SRC_KINDS = {"bank_page", "kfs", "tc_pdf", "aggregator", "community"}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(card: dict, issuer: str) -> list[str]:
    errs: list[str] = []
    cid = card.get("id", "?")

    def need(cond: bool, msg: str) -> None:
        if not cond:
            errs.append(f"{cid}: {msg}")

    need(bool(ID_RE.match(str(card.get("id", "")))), "bad id")
    need(card.get("issuer") == issuer, "issuer mismatch with file")
    need(bool(card.get("name")), "name missing")
    need(card.get("network") in NETWORKS, "bad network")
    need(card.get("status") in STATUS, "bad status")
    need(card.get("confidence") in CONF, "bad confidence")
    for k in ("annual_fee_minor", "joining_fee_minor", "min_salary_minor"):
        v = card.get(k)
        need(v is None or (isinstance(v, int) and v >= 0), f"{k} must be int fils or null")
    try:
        parse_structure(card.get("reward_structure") or {})
    except Exception as e:  # noqa: BLE001
        errs.append(f"{cid}: reward_structure invalid: {e}")
    srcs = card.get("sources") or []
    need(len(srcs) > 0, "needs at least one source")
    for s in srcs:
        need(s.get("kind") in SRC_KINDS, f"bad source kind {s.get('kind')}")
        need(str(s.get("url", "")).startswith("https://"), "source url must be https")
        need(bool(DATE_RE.match(str(s.get("verified_on", "")))), "source verified_on must be YYYY-MM-DD")
    if card.get("confidence") == "high":
        need(
            any(s.get("kind") in {"bank_page", "kfs", "tc_pdf"} for s in srcs),
            "high confidence needs an official source",
        )
    return errs


def build() -> tuple[dict, list[str]]:
    cards: list[dict] = []
    errs: list[str] = []
    art = json.loads(ART.read_text()) if ART.exists() else {}
    seen: set[str] = set()
    for f in sorted(BANKS.glob("*.json")):
        data = json.loads(f.read_text())
        for c in data["cards"]:
            errs += validate(c, data["issuer"])
            if c.get("id") in seen:
                errs.append(f"{c.get('id')}: duplicate id")
            seen.add(c.get("id"))
            c.pop("art", None)
            errs += validate_art(art.get(c.get("id")), c)
            if c.get("id") in art:
                c["art"] = art[c["id"]]
            cards.append(c)
    cards.sort(key=lambda c: c["id"])
    newest = max((s["verified_on"] for c in cards for s in c.get("sources", [])), default=str(date.today()))
    return {
        "schema_version": 1,
        "catalog_version": newest,
        "currency": "AED",
        "count": len(cards),
        "cards": cards,
    }, errs


def check_stacking(catalog: dict) -> list[str]:
    path = ROOT / "data" / "cards" / "stacking.json"
    ids = {c["id"] for c in catalog["cards"]}
    errs = []
    for m in json.loads(path.read_text())["merchants"]:
        for p in m.get("plays", []):
            if p.get("card_id") not in ids:
                errs.append(f"stacking {m['id']}: unknown card_id {p.get('card_id')}")
    return errs


def main() -> int:
    catalog, errs = build()
    errs += check_stacking(catalog)
    if "--validate" in sys.argv:
        print("\n".join(errs) or f"ok: {catalog['count']} cards")
        return 1 if errs else 0
    if errs:
        print("\n".join(errs))
        return 1
    text = json.dumps(catalog, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        return 0 if OUT.exists() and OUT.read_text() == text else (print("catalog.json is stale") or 1)
    OUT.write_text(text)
    print(f"wrote {catalog['count']} cards, version {catalog['catalog_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

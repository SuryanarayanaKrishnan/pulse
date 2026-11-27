"""Curated card intel (tips, caveats, combos, offer changes) tied to catalog card ids.

Source of truth is ``data/cards/intel.json`` (reviewed by a human; the Reddit scraper only proposes
candidates). Everything here is pure and free: no network, no LLM. The app shows items matched to the
user's cards, and the recommender reads ``recommender_adjustments`` so discovered stacking and temporary
caveats change which card it suggests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.services import combo_flow

ROOT = Path(__file__).resolve().parents[2]
INTEL_PATH = ROOT / "data" / "cards" / "intel.json"

KINDS = {"caveat", "combo", "bonus", "offer_change"}
SEVERITIES = {"info", "warn"}
CONFIDENCES = {"reported", "corroborated", "verified"}
STATUSES = {"active", "watching", "retired"}
# free: shown to everyone, no cards needed. plus: niche detail (rare combos, time-bound caveats) behind the
# paywall. The server labels tiers; the app decides what the user may see (see docs/ENTITLEMENT.md).
TIERS = {"free", "plus"}
DEFAULT_TIER = "plus"
# How much the recommender trusts an item. A single unverified report only nudges, never hides a card.
TRUST = {"reported": 0.5, "corroborated": 0.8, "verified": 1.0}


def load_intel(path: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((path or INTEL_PATH).read_text())
    return data


def validate(
    intel: dict[str, Any],
    known_card_ids: set[str] | None = None,
    cards: dict[str, dict[str, Any]] | None = None,
    claims: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    errs: list[str] = []
    seen: set[str] = set()
    for it in intel.get("items", []):
        iid = it.get("id", "?")
        if iid in seen:
            errs.append(f"{iid}: duplicate id")
        seen.add(iid)
        if it.get("kind") not in KINDS:
            errs.append(f"{iid}: bad kind")
        if it.get("severity") not in SEVERITIES:
            errs.append(f"{iid}: bad severity")
        if it.get("confidence") not in CONFIDENCES:
            errs.append(f"{iid}: bad confidence")
        if it.get("status") not in STATUSES:
            errs.append(f"{iid}: bad status")
        if it.get("tier", DEFAULT_TIER) not in TIERS:
            errs.append(f"{iid}: bad tier")
        if not it.get("card_ids"):
            errs.append(f"{iid}: no card_ids")
        if not it.get("sources"):
            errs.append(f"{iid}: needs a source")
        for k in ("valid_from", "valid_until", "review_by", "first_seen", "last_confirmed"):
            v = it.get(k)
            if v:
                try:
                    date.fromisoformat(v)
                except ValueError:
                    errs.append(f"{iid}: {k} not an ISO date")
        if known_card_ids is not None:
            for cid in it.get("card_ids", []):
                if cid not in known_card_ids:
                    errs.append(f"{iid}: unknown card id {cid}")
        eff = it.get("effect")
        if eff and eff.get("type") == "rate_override" and not 0 <= eff.get("rate", -1) < 0.5:
            errs.append(f"{iid}: effect rate out of range")
        errs += _validate_flow(it, cards, claims)
    return errs


def _validate_flow(
    it: dict[str, Any], cards: dict[str, dict[str, Any]] | None, claims: dict[str, dict[str, Any]] | None
) -> list[str]:
    """A saving figure only ever comes from a flow. Hand-typed numbers cannot be shipped."""
    iid = it.get("id", "?")
    flow = it.get("flow")
    errs: list[str] = []
    for k in ("annual_saving_aed", "saving_note"):
        if k in it:
            errs.append(f"{iid}: {k} must be computed from a flow, not typed")
    if not flow:
        return errs
    if re.search(r"\d", it.get("body", "")):
        errs.append(f"{iid}: body of a flow combo must not hold numbers; the flow computes them")
    if cards is None or claims is None:
        return errs
    try:
        out = combo_flow.evaluate(flow, cards, claims)
    except (ValueError, KeyError) as e:
        return [*errs, f"{iid}: flow does not evaluate: {e}"]
    order = ["verified", "corroborated", "reported"]
    if order.index(it.get("confidence", "reported")) < order.index(out["confidence"]):
        errs.append(f"{iid}: confidence {it.get('confidence')} is stronger than its claims allow")
    step_cards = {s["card_id"] for s in flow["steps"]}
    if not step_cards <= set(it.get("card_ids", [])):
        errs.append(f"{iid}: card_ids must include every card in the flow")
    return errs


def is_active(item: dict[str, Any], today: date) -> bool:
    if item.get("status") == "retired":
        return False
    start, end = item.get("valid_from"), item.get("valid_until")
    if start and today < date.fromisoformat(start):
        return False
    return not (end and today > date.fromisoformat(end))


@dataclass
class IntelForUser:
    item: dict[str, Any]
    owned_card_ids: list[str]
    missing_card_ids: list[str] = field(default_factory=list)

    def out(self) -> dict[str, Any]:
        it = self.item
        base = self._base()
        if it.get("flow"):
            base.update(flow_output(it))
        return base

    def _base(self) -> dict[str, Any]:
        it = self.item
        return {
            "id": it["id"],
            "kind": it["kind"],
            "title": it["title"],
            "body": it["body"],
            "severity": it["severity"],
            "tier": it.get("tier", DEFAULT_TIER),
            "confidence": it["confidence"],
            "card_ids": it["card_ids"],
            "owned_card_ids": self.owned_card_ids,
            "missing_card_ids": self.missing_card_ids,
            "valid_until": it.get("valid_until"),
            "last_confirmed": it.get("last_confirmed"),
            "sources": [s.get("url") or s.get("ref") for s in it["sources"]],
        }


_FLOW_CACHE: dict[str, Any] = {}


def flow_output(it: dict[str, Any]) -> dict[str, Any]:
    """Computed fields for a flow combo: steps, breakdown, figure, confidence and why."""
    if "cards" not in _FLOW_CACHE:
        catalog = json.loads((ROOT / "data" / "cards" / "catalog.json").read_text())
        _FLOW_CACHE["cards"] = {c["id"]: c for c in catalog["cards"]}
        _FLOW_CACHE["claims"] = combo_flow.load_claims()
    r = combo_flow.evaluate(it["flow"], _FLOW_CACHE["cards"], _FLOW_CACHE["claims"])
    return {
        "confidence": r["confidence"],
        "confidence_label": r["confidence_label"],
        "steps": r["steps"],
        "breakdown": r["breakdown"],
        "annual_saving_aed": r["annual_saving_aed"],
        "saving_note": r["saving_note"],
        "claims": [
            {
                k: _FLOW_CACHE["claims"][c][k]
                for k in ("id", "statement", "confidence", "evidence", "source_url", "checked_on")
            }
            for c in r["claims_used"]
        ],
    }


def intel_for_cards(
    intel: dict[str, Any], owned: set[str], today: date, *, include_combo_suggestions: bool = True
) -> list[IntelForUser]:
    """Active items touching the user's cards. Caveats/offer changes need an owned card; combos also show
    when the user holds only one half, so the app can suggest the missing card."""
    out: list[IntelForUser] = []
    for it in intel.get("items", []):
        if not is_active(it, today):
            continue
        have = [c for c in it["card_ids"] if c in owned]
        if not have:
            continue
        missing = [c for c in it["card_ids"] if c not in owned]
        if it["kind"] == "combo" and missing and not include_combo_suggestions:
            continue
        if it["kind"] != "combo":
            missing = []
        out.append(IntelForUser(it, have, missing))
    out.sort(key=lambda r: (r.item["severity"] != "warn", r.item["kind"], r.item["id"]))
    return out


def starter_items(intel: dict[str, Any], today: date) -> list[IntelForUser]:
    """Active items for everyone, owned cards or not: the default combos a brand-new user sees.

    Free items come first, then plus items (the app shows those as locked teasers until entitled).
    Nothing here depends on the user, so the response is cacheable.
    """
    out = [
        IntelForUser(it, [], [c for c in it["card_ids"]] if it["kind"] == "combo" else [])
        for it in intel.get("items", [])
        if is_active(it, today) and it["kind"] in {"combo", "offer_change", "bonus", "caveat"}
    ]
    out.sort(
        key=lambda r: (
            r.item.get("tier", DEFAULT_TIER) != "free",
            r.item["kind"] != "combo",
            r.item["id"],
        )
    )
    return out


def recommender_adjustments(intel: dict[str, Any], today: date) -> list[dict[str, Any]]:
    """Rate overrides and combo pairings the rewards engine can apply, each with a trust weight.

    The engine blends: ``rate = trust * override + (1 - trust) * catalog_rate`` so one unverified report
    lowers a card's rank without removing it.
    """
    out = []
    for it in intel.get("items", []):
        eff = it.get("effect")
        if not eff or not is_active(it, today):
            continue
        out.append({"item_id": it["id"], "trust": TRUST[it["confidence"]], **eff})
    return out

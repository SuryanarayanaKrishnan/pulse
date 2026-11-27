"""Combos as explicit money flows, so the mechanic and the maths come from one structure.

A combo with a ``flow`` never carries a hand-written saving or a hand-written step list. Its steps
say who pays whom (``spend`` on a card, ``repay`` one card's bill with another) and every rate, cap
and fee is either read from the card catalog or from ``data/cards/claims.json``, where it carries its
source and confidence. This module turns the flow into the readable steps, the monthly and yearly
breakdown and the confidence of the whole figure (the weakest claim it leans on).

Pure and free: no network, no database. Money is plain AED floats here because the output is an
estimate shown to people, never a ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CLAIMS_PATH = ROOT / "data" / "cards" / "claims.json"

CONFIDENCE_ORDER = ["verified", "reported", "community", "unverified"]  # strongest first
EVIDENCE = {"official", "aggregator", "community", "assumption"}
# How the weakest claim reads in the combo's own confidence field (the app's three labels).
ITEM_CONFIDENCE = {
    "verified": "verified",
    "reported": "reported",
    "community": "reported",
    "unverified": "reported",
}
CONFIDENCE_LABEL = {
    "verified": "Checked against the bank's own page",
    "reported": "From a comparison site or a single source, not checked against the bank",
    "community": "Based on community reports, not confirmed by the bank",
    "unverified": "Includes an assumption or a detail we could not confirm",
}


def load_claims(path: Path | None = None) -> dict[str, dict[str, Any]]:
    data = json.loads((path or CLAIMS_PATH).read_text())
    return {c["id"]: c for c in data["claims"]}


def weakest(levels: list[str]) -> str:
    return max(levels, key=CONFIDENCE_ORDER.index) if levels else "verified"


def _card(cards: dict[str, dict[str, Any]], cid: str) -> dict[str, Any]:
    if cid not in cards:
        raise ValueError(f"unknown card {cid}")
    card: dict[str, Any] = cards[cid]
    return card


def _rule(card: dict[str, Any], name: str) -> dict[str, Any]:
    for r in card["reward_structure"].get("rules", []):
        if r.get("name") == name:
            rule: dict[str, Any] = r
            return rule
    raise ValueError(f"{card['id']}: no rule named {name!r}")


def catalog_value(cards: dict[str, dict[str, Any]], ref: dict[str, Any]) -> float:
    """The number a claim points at in the catalog (rule field, card field or reward field)."""
    card = _card(cards, ref["card_id"])
    if "rule" in ref:
        v = _rule(card, ref["rule"]).get(ref["field"])
    elif ref["field"] in card:
        v = card[ref["field"]]
    else:
        v = card["reward_structure"].get(ref["field"])
    if v is None:
        raise ValueError(f"{ref}: not in catalog")
    return float(v) * float(ref.get("scale", 1))


def check_claims(claims: dict[str, dict[str, Any]], cards: dict[str, dict[str, Any]]) -> list[str]:
    """Structure of the claims file and agreement with the catalog numbers they mirror."""
    errs: list[str] = []
    for c in claims.values():
        cid = c.get("id", "?")
        if c.get("evidence") not in EVIDENCE:
            errs.append(f"{cid}: bad evidence")
        if c.get("confidence") not in CONFIDENCE_ORDER:
            errs.append(f"{cid}: bad confidence")
        if not str(c.get("source_url", "")).startswith("https://"):
            errs.append(f"{cid}: needs an https source_url")
        if not c.get("checked_on"):
            errs.append(f"{cid}: needs checked_on")
        if c.get("card_id") and c["card_id"] not in cards:
            errs.append(f"{cid}: unknown card {c['card_id']}")
        # A claim may only be 'verified' when an official source was read and a quote is held.
        if c.get("confidence") == "verified" and (c.get("evidence") != "official" or not c.get("quote")):
            errs.append(f"{cid}: verified needs official evidence and a quote")
        if c.get("evidence") in {"community", "assumption"} and c.get("confidence") in {
            "verified",
            "reported",
        }:
            errs.append(f"{cid}: {c.get('evidence')} evidence cannot be {c.get('confidence')}")
        ref = c.get("catalog_ref")
        if ref:
            try:
                have = catalog_value(cards, ref)
            except (ValueError, KeyError) as e:
                errs.append(f"{cid}: catalog_ref broken: {e}")
                continue
            if abs(have - float(c["value"])) > 1e-9:
                errs.append(f"{cid}: claim says {c['value']} but the catalog has {have}")
    return errs


def _need_claim(claims: dict[str, dict[str, Any]], cid: str) -> dict[str, Any]:
    if cid not in claims:
        raise ValueError(f"unknown claim {cid}")
    return claims[cid]


def evaluate(
    flow: dict[str, Any], cards: dict[str, dict[str, Any]], claims: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Work a flow out. Returns steps, breakdown lines, yearly figure, confidence and a note."""
    steps = flow["steps"]
    amount: dict[str, float] = {}
    lines: list[dict[str, Any]] = []
    used: list[str] = []  # claim ids the counted figure leans on
    readable: list[dict[str, Any]] = []

    def add_claim(cid: str) -> dict[str, Any]:
        c = _need_claim(claims, cid)
        used.append(cid)
        return c

    # Amount that flows through each step, resolved in order. A step may be limited by the cap of the
    # step that repays it (the chain only earns on what the cap can pay for).
    def rate_and_cap(step: dict[str, Any]) -> tuple[float, float | None]:
        earn = step.get("earn")
        if not earn:
            return 0.0, None
        rule = _rule(_card(cards, step["card_id"]), earn["catalog_rule"])
        return float(rule["rate"]), (
            float(rule["monthly_cap_units"]) if rule.get("monthly_cap_units") else None
        )

    for s in steps:
        if s["kind"] == "spend":
            a = float(add_claim(s["amount_claim"])["value"])
            for other in steps:
                if other["kind"] == "repay" and other.get("pays") == s["id"] and other.get("counted", True):
                    rate, cap = rate_and_cap(other)
                    if cap is not None and rate > 0:
                        a = min(a, cap / rate)
            amount[s["id"]] = a
        elif s["kind"] == "repay":
            amount[s["id"]] = amount[s["pays"]]
        else:
            raise ValueError(f"{s['id']}: bad kind {s['kind']}")

    for s in steps:
        card = _card(cards, s["card_id"])
        name = f"{card['issuer']} {card['name']}"
        a = amount[s["id"]]
        text = s["do"]
        entry = {"id": s["id"], "text": text, "card_id": s["card_id"], "counted": s.get("counted", True)}
        if not entry["counted"]:
            entry["why_not_counted"] = _need_claim(claims, s["claims"][0])["statement"]
            entry["confidence"] = _need_claim(claims, s["claims"][0])["confidence"]
        readable.append(entry)
        if s["kind"] == "repay" and s.get("counted", True):
            rate, cap = rate_and_cap(s)
            earned = a * rate
            if cap is not None:
                earned = min(earned, cap)
            rule_name = s["earn"]["catalog_rule"]
            for cl in s["earn"].get("claims", []):
                add_claim(cl)
            lines.append(
                {
                    "kind": "earn",
                    "label": f"{name} cashback: {rate:.0%} on AED {a:,.0f}"
                    + (f", capped at AED {cap:,.0f} a month" if cap is not None else ""),
                    "monthly_aed": round(earned, 2),
                    "claims": [*s["earn"].get("claims", []), *s.get("claims", [])],
                    "rule": rule_name,
                }
            )
            fee = s.get("fee")
            if fee:
                fc = add_claim(fee["claim"])
                lines.append(
                    {
                        "kind": "fee",
                        "label": f"Fee to repay by card: {float(fc['value']):.0%} of AED {a:,.0f}",
                        "monthly_aed": round(-a * float(fc["value"]), 2),
                        "claims": [fee["claim"]],
                    }
                )

    for fx in flow.get("yearly_costs", []):
        c = add_claim(fx["claim"])
        lines.append(
            {
                "kind": "annual_fee",
                "label": fx["label"],
                "monthly_aed": round(-float(c["value"]) / 12, 4),
                "yearly_aed": -float(c["value"]),
                "claims": [fx["claim"]],
                "note": fx.get("note"),
            }
        )

    for line in lines:
        line.setdefault("yearly_aed", round(line["monthly_aed"] * 12, 2))
        line["confidence"] = weakest([claims[c]["confidence"] for c in line["claims"]])

    net = round(sum(line["yearly_aed"] for line in lines), 2)
    # Our own spending assumption is listed as an assumption; it does not weaken what the bank said.
    conf = weakest([claims[c]["confidence"] for c in used if claims[c]["evidence"] != "assumption"])
    assumptions = [
        claims[c]["statement"] for c in dict.fromkeys(used) if claims[c]["evidence"] == "assumption"
    ]
    return {
        "steps": readable,
        "breakdown": lines,
        "annual_saving_aed": net,
        "confidence_level": conf,
        "confidence": ITEM_CONFIDENCE[conf],
        "confidence_label": CONFIDENCE_LABEL[conf],
        "claims_used": list(dict.fromkeys(used)),
        "assumptions": assumptions,
        "saving_note": saving_note(lines, net, assumptions),
    }


def saving_note(lines: list[dict[str, Any]], net: float, assumptions: list[str]) -> str:
    parts = []
    for line in lines:
        sign = "+" if line["yearly_aed"] >= 0 else "-"
        parts.append(f"{sign} AED {abs(line['yearly_aed']):,.0f} a year: {line['label']}")
    text = "Estimate, not a promise. " + "; ".join(parts) + f". Net about AED {net:,.0f} a year."
    if assumptions:
        text += " Assumes: " + " ".join(assumptions)
    return text

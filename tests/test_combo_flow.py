"""Combos are money flows; figures come from code and sourced claims, never typed copy."""

import copy
import json
from datetime import date
from pathlib import Path

from app.services import card_intel as ci
from app.services import combo_flow as cf

ROOT = Path(__file__).resolve().parent.parent
CARDS = {c["id"]: c for c in json.loads((ROOT / "data/cards/catalog.json").read_text())["cards"]}
CLAIMS = cf.load_claims()
INTEL = ci.load_intel()
CHAIN = next(i for i in INTEL["items"] if i["id"] == "reddit-tabby-sib-chain")


def test_claims_are_well_formed_and_match_the_catalog():
    assert cf.check_claims(CLAIMS, CARDS) == []


def test_tabby_sib_chain_worked_example():
    # AED 3,000 a month through Tabby, repaid by SIB at 10% (AED 300 cap), 1% Tabby fee, AED 199 SIB fee.
    r = cf.evaluate(CHAIN["flow"], CARDS, CLAIMS)
    assert r["annual_saving_aed"] == 3600 - 360 - 199 == 3041
    kinds = {line["kind"]: line["yearly_aed"] for line in r["breakdown"]}
    assert kinds == {"earn": 3600, "fee": -360, "annual_fee": -199}
    # Step 3 (pay the SIB bill with another card) is shown but never counted.
    assert [s["counted"] for s in r["steps"]] == [True, True, False]
    assert "why_not_counted" in r["steps"][2]


def test_figure_is_only_as_strong_as_its_weakest_claim():
    r = cf.evaluate(CHAIN["flow"], CARDS, CLAIMS)
    assert r["confidence_level"] == "community"  # Reddit-only mechanic and Tabby fee
    assert r["confidence"] == "reported"  # the app never calls it verified or corroborated
    assert "community" in r["confidence_label"].lower()


def test_cap_limits_the_amount_that_earns():
    flow = copy.deepcopy(CHAIN["flow"])
    claims = copy.deepcopy(CLAIMS)
    claims["app.typical_month_nonbill_spend"]["value"] = 50000  # far more spend than the cap can pay on
    r = cf.evaluate(flow, CARDS, claims)
    assert r["annual_saving_aed"] == 3041  # still capped at AED 300 a month


def test_small_spend_earns_proportionally():
    claims = copy.deepcopy(CLAIMS)
    claims["app.typical_month_nonbill_spend"]["value"] = 1000
    r = cf.evaluate(CHAIN["flow"], CARDS, claims)
    assert r["annual_saving_aed"] == round(1000 * 0.10 * 12 - 1000 * 0.01 * 12 - 199, 2)


def test_changing_a_catalog_rate_changes_the_figure_and_breaks_the_claim():
    cards = copy.deepcopy(CARDS)
    rule = next(
        r
        for r in cards["sib-cashback"]["reward_structure"]["rules"]
        if r["name"] == "Online and wallet spend"
    )
    rule["rate"] = 0.05
    assert cf.evaluate(CHAIN["flow"], cards, CLAIMS)["annual_saving_aed"] != 3041
    assert any("sib.online_wallet_rate" in e for e in cf.check_claims(CLAIMS, cards))


def test_validator_rejects_typed_figures_numbers_in_copy_and_overconfidence():
    def check(**over):
        it = copy.deepcopy(CHAIN) | over
        return ci.validate({"items": [it]}, set(CARDS), CARDS, CLAIMS)

    assert check() == []
    assert any("computed from a flow" in e for e in check(annual_saving_aed=3041))
    assert any("must not hold numbers" in e for e in check(body="Earn 10% back."))
    assert any("stronger than its claims" in e for e in check(confidence="verified"))
    bad = copy.deepcopy(CHAIN)
    bad["flow"]["steps"][1]["earn"]["claims"].append("nope")
    assert any("unknown claim" in e for e in ci.validate({"items": [bad]}, set(CARDS), CARDS, CLAIMS))


def test_claim_rules_forbid_overstating_evidence():
    claims = copy.deepcopy(CLAIMS)
    claims["tabby.card_repayment_fee"]["confidence"] = "verified"
    errs = cf.check_claims(claims, CARDS)
    assert any("verified needs official evidence" in e for e in errs)


def test_export_carries_steps_breakdown_and_claims():
    out = next(
        r.out()
        for r in ci.starter_items(INTEL, date(2026, 11, 28))
        if r.item["id"] == "reddit-tabby-sib-chain"
    )
    assert out["annual_saving_aed"] == 3041
    assert [b["kind"] for b in out["breakdown"]] == ["earn", "fee", "annual_fee"]
    assert out["confidence"] == "reported"
    assert {c["id"] for c in out["claims"]} >= {"sib.annual_fee", "tabby.card_repayment_fee"}

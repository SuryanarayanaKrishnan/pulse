"""Pure rule-engine tests (no database)."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.transaction import Direction, TxnSource
from app.schemas.rule import RuleCreate, RulePreviewIn
from app.services.categorisation import CategoryResolver
from app.services.rule_engine import TxnFacts, check_regex, compile_rule, evaluate

ACC = uuid4()


def facts(**over):
    base = {
        "merchant_raw": "CARREFOUR MOE DUBAI AE",
        "merchant_normalized": "Carrefour",
        "notes": None,
        "amount_minor": 12_500,
        "direction": Direction.debit,
        "account_id": ACC,
        "source": TxnSource.sms,
    }
    return TxnFacts(**{**base, **over})


def rule(*conds, **kw):
    return compile_rule(list(conds), **kw)


def m(op, value, field="merchant"):
    return {"field": field, "op": op, "value": value}


def test_text_conditions_are_case_insensitive_and_cover_normalised_and_raw():
    assert rule(m("contains", "carre")).matches(facts())
    assert rule(m("contains", "MOE DUBAI")).matches(facts())  # raw descriptor
    assert rule(m("equals", "  carrefour ")).matches(facts())
    assert not rule(m("equals", "carre")).matches(facts())
    assert rule(m("regex", r"^carre\w+$")).matches(facts())
    assert not rule(m("regex", r"^lulu")).matches(facts())


def test_description_looks_at_raw_text_and_notes():
    f = facts(merchant_raw="POS 123 SOME SHOP", notes="birthday gift")
    assert rule(m("contains", "birthday", "description")).matches(f)
    assert rule(m("contains", "some shop", "description")).matches(f)
    assert rule(m("equals", "BIRTHDAY GIFT", "description")).matches(f)
    assert not rule(m("contains", "carrefour", "description")).matches(f)
    assert not rule(m("contains", "x", "description")).matches(facts(merchant_raw=None))


def test_amount_conditions():
    f = facts(amount_minor=5000)
    amount = lambda **k: {"field": "amount", **k}  # noqa: E731
    assert rule(amount(op="equals", value_minor=5000)).matches(f)
    assert not rule(amount(op="equals", value_minor=5001)).matches(f)
    assert rule(amount(op="between", min_minor=5000, max_minor=6000)).matches(f)
    assert rule(amount(op="between", min_minor=1, max_minor=5000)).matches(f)
    assert not rule(amount(op="between", min_minor=5001, max_minor=6000)).matches(f)
    assert rule(amount(op="gte", value_minor=5000)).matches(f)
    assert rule(amount(op="lte", value_minor=5000)).matches(f)
    assert not rule(amount(op="gte", value_minor=5001)).matches(f)
    assert not rule(amount(op="equals", value_minor=5000)).matches(facts(amount_minor=None))


def test_direction_account_source_and_and_semantics():
    both = rule(
        {"field": "direction", "value": "debit"},
        {"field": "account", "account_id": str(ACC)},
        {"field": "source", "value": "sms"},
        m("contains", "carrefour"),
    )
    assert both.matches(facts())
    assert not both.matches(facts(direction=Direction.credit))
    assert not both.matches(facts(account_id=uuid4()))
    assert not both.matches(facts(source=TxnSource.manual))
    assert not both.matches(facts(merchant_normalized="Lulu", merchant_raw="LULU"))
    assert not compile_rule([]).matches(facts())  # no conditions never matches


def test_priority_first_rule_wins_per_field_tags_accumulate():
    a, b, c = uuid4(), uuid4(), uuid4()
    r1 = rule(m("contains", "carre"), id=a, category_id=b, tags=("food",))
    r2 = rule(
        m("contains", "carre"), id=b, category_id=c, rename_merchant="Carrefour MOE", tags=("food", "weekly")
    )
    r3 = rule(m("contains", "nothing"), id=c, category_id=a)
    out = evaluate([r1, r2, r3], facts())
    assert out is not None
    assert out.category_id == b and out.first_rule_id == a  # r1 decided the category
    assert out.rename_merchant == "Carrefour MOE"  # r2 supplied the first rename
    assert out.tags == ["food", "weekly"] and out.rule_ids == [a, b]
    assert evaluate([r3], facts()) is None


def test_mark_transfer_resolves_to_transfer_category_and_excludes():
    transfer, shopping = uuid4(), uuid4()
    resolver = CategoryResolver(
        [rule(m("contains", "wise"), id=uuid4(), mark_transfer=True)],
        {"transfer": transfer, "shopping": shopping},
    )
    e = resolver.enrich("WISE PAYMENTS LTD", Direction.debit)
    assert e.category_id == transfer and e.exclude_from_budget is True and e.source == "rule"
    explicit = CategoryResolver(
        [rule(m("contains", "wise"), id=uuid4(), mark_transfer=True, category_id=shopping)],
        {"transfer": transfer},
    )
    assert explicit.enrich("WISE", Direction.debit).category_id == shopping


def test_rule_without_category_keeps_dictionary_category_but_applies_tags_and_rename():
    groceries = uuid4()
    r = CategoryResolver(
        [rule(m("contains", "carrefour"), id=uuid4(), tags=("home",), rename_merchant="Weekly shop")],
        {"groceries": groceries},
    )
    e = r.enrich("CARREFOUR MOE", Direction.debit)
    assert (e.category_id, e.source, e.merchant_normalized, e.tags) == (
        groceries,
        "dictionary",
        "Weekly shop",
        ("home",),
    )


def test_resolver_passes_context_to_conditions():
    big, other = uuid4(), uuid4()
    res = CategoryResolver(
        [rule({"field": "amount", "op": "gte", "value_minor": 100_000}, category_id=big)], {"other": other}
    )
    assert res.enrich("JOES CAFE", Direction.debit, amount_minor=150_000).category_id == big
    assert res.enrich("JOES CAFE", Direction.debit, amount_minor=1_000).category_id is None


@pytest.mark.parametrize("pattern", ["(a+)+$", "(.*)*x", r"(\d+)*", "x" * 201, "(unclosed", "[z-a]"])
def test_unsafe_or_invalid_regex_is_rejected(pattern):
    with pytest.raises(ValueError):
        check_regex(pattern)


def test_reasonable_regex_is_accepted():
    check_regex(r"^(?:salik|darb)\b.*\d{3}$")
    check_regex(r"(?:carrefour|lulu)\s+(?:hyper)?market")


def test_rule_schema_validation():
    ok = {"conditions": [{"field": "merchant", "op": "contains", "value": "x"}], "category_id": str(uuid4())}
    assert RuleCreate.model_validate(ok).priority == 100
    for bad in (
        {**ok, "conditions": []},  # needs a condition
        {"conditions": ok["conditions"]},  # needs an action
        {**ok, "conditions": [{"field": "merchant", "op": "regex", "value": "(a+)+"}]},
        {**ok, "conditions": [{"field": "amount", "op": "between", "min_minor": 5, "max_minor": 1}]},
        {**ok, "conditions": [{"field": "amount", "op": "equals"}]},
        {**ok, "conditions": [{"field": "direction", "value": "sideways"}]},
        {**ok, "conditions": [{"field": "bogus", "value": 1}]},
        {**ok, "tags": ["x"] * 11},
    ):
        with pytest.raises(ValidationError):
            RuleCreate.model_validate(bad)
    assert (
        RulePreviewIn.model_validate({"conditions": ok["conditions"]}).limit == 50
    )  # preview needs no action

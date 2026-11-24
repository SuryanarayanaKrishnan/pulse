from uuid import uuid4

import pytest

from app.models.transaction import Direction
from app.services.categorisation import CategoryResolver
from app.services.merchants import clean_descriptor, guess_category_slug, merchant_key, normalize_merchant
from app.services.rule_engine import merchant_equals_rule

DEBIT, CREDIT = Direction.debit, Direction.credit


@pytest.mark.parametrize(
    ("raw", "name", "category"),
    [
        ("CARREFOUR MOE DUBAI AE", "Carrefour", "groceries"),
        ("MAF CARREFOUR HYPERMARKET 0123", "Carrefour", "groceries"),
        ("POS-LULU HYPERMARKET AL BARSHA 1234", "Lulu", "groceries"),
        ("TALABAT DUBAI ARE", "Talabat", "dining"),
        ("DELIVEROO.AE", "Deliveroo", "dining"),
        ("CAREEM *RIDE DUBAI", "Careem", "transport"),
        ("CAREEM FOOD", "Careem Food", "dining"),
        ("UBER *EATS", "Uber Eats", "dining"),
        ("PAYFORT*NOON.COM", "Noon", "shopping"),
        ("NOON FOOD", "Noon Food", "dining"),
        ("AMAZON.AE", "Amazon.ae", "shopping"),
        ("AMZN MKTP AE", "Amazon.ae", "shopping"),
        ("ENOC STN 123 DUBAI", "ENOC", "transport"),
        ("ADNOC DISTRIBUTION", "ADNOC", "transport"),
        ("DEWA 98765", "DEWA", "utilities"),
        ("SEWA BILL PAYMENT", "SEWA", "utilities"),
        ("ETISALAT", "e&", "telecom"),
        ("E& (ETISALAT) DUBAI", "e&", "telecom"),
        ("DU TELECOM", "du", "telecom"),
        ("SALIK RECHARGE", "Salik", "transport"),
        ("RTA NOL TOP UP", "RTA", "transport"),
        ("TABBY FZ-LLC", "Tabby", "shopping"),
        ("TAMARA", "Tamara", "shopping"),
        ("NETFLIX.COM", "Netflix", "fixed_expenses"),
        ("EMIRATES AIRLINE", "Emirates Airline", "travel"),
    ],
)
def test_dictionary_hits(raw, name, category):
    assert normalize_merchant(raw, DEBIT) == name
    assert guess_category_slug(raw, DEBIT) == category


def test_unknown_merchant_falls_back_to_cleaned_title_case():
    assert normalize_merchant("Refund Co") == "Refund Co"
    assert normalize_merchant("JOES CAFE DUBAI AE 12345") == "Joes Cafe"
    assert guess_category_slug("JOES CAFE") is None
    assert normalize_merchant("") is None and normalize_merchant(None) is None


def test_no_false_positive_on_substrings():
    # "EMIRATES NBD" must not become the airline; "DUBAI" alone must not be "du".
    assert guess_category_slug("EMIRATES NBD") is None
    assert guess_category_slug("DUBAI COFFEE ROASTERS") is None
    assert guess_category_slug("MAXIM'S CAFE") is None


def test_direction_sensitive_entries():
    assert guess_category_slug("SALARY JAN", CREDIT) == "salary"
    assert guess_category_slug("PAYMENT RECEIVED - THANK YOU", CREDIT) == "cc_payment"
    assert guess_category_slug("ANNUAL FEE", DEBIT) == "fees"
    assert guess_category_slug("ANNUAL FEE", CREDIT) is None


def test_clean_descriptor_strips_noise():
    assert clean_descriptor("POS PURCHASE  PAYFORT*NOON DUBAI AE") == "NOON"
    assert clean_descriptor("shop  1234") == "SHOP"
    assert merchant_key("  Carrefour  ") == "carrefour"
    assert merchant_key(None) is None


def test_resolver_rules_beat_dictionary():
    groceries, dining, shopping = uuid4(), uuid4(), uuid4()
    system = {"groceries": groceries, "dining": dining, "shopping": shopping}
    plain = CategoryResolver([], system)
    assert plain.enrich("CARREFOUR MOE", DEBIT).category_id == groceries
    assert plain.enrich("CARREFOUR MOE", DEBIT).source == "dictionary"
    ruled = CategoryResolver([merchant_equals_rule("carrefour", shopping)], system)
    e = ruled.enrich("CARREFOUR MOE", DEBIT)
    assert (e.merchant_normalized, e.category_id, e.source) == ("Carrefour", shopping, "rule")
    unknown = ruled.enrich("JOES CAFE", DEBIT)
    assert unknown.category_id is None and unknown.source is None
    # rule for an otherwise-unknown merchant
    joes = CategoryResolver([merchant_equals_rule("joes cafe", dining)], system)
    assert joes.enrich("JOES CAFE", DEBIT).category_id == dining


@pytest.mark.parametrize(
    ("raw", "direction", "slug"),
    [
        ("HOME LOAN INSTALMENT", DEBIT, "home_loan"),
        ("MORTGAGE PAYMENT", DEBIT, "home_loan"),
        ("CAR LOAN EMI", DEBIT, "car_loan"),
        ("AL ANSARI EXCHANGE", DEBIT, "family"),
        ("WESTERN UNION MONEY TRANSFER", DEBIT, "family"),
        ("ORIENT INSURANCE PREMIUM", DEBIT, "fixed_expenses"),
        ("MOBILE TRANSFER", DEBIT, "transfer"),
    ],
)
def test_new_default_categories_are_reachable_from_the_dictionary(raw, direction, slug):
    assert guess_category_slug(raw, direction) == slug


def test_every_dictionary_slug_is_a_seeded_default():
    import importlib.util
    from pathlib import Path

    from app.services.merchants import DICTIONARY

    path = next(Path("alembic/versions").glob("*_0003_*.py"))
    spec = importlib.util.spec_from_file_location("m0003", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seeded = {d[0] for d in mod.DEFAULTS}
    assert {e.category for e in DICTIONARY} <= seeded


@pytest.mark.parametrize(
    ("raw", "direction", "name", "slug"),
    [
        (
            "IPP TRANSFER 000000000000400000XXXXXX0001 - Jane - /REF/ Credit card payment",
            DEBIT,
            "Credit Card Payment",
            "cc_payment",
        ),
        ("VALUE DATE: 01-07-2026 Interest 000000000002", Direction.credit, "Interest Income", "other_income"),
        (
            "Inward Remittance SAMPLE SENDER NAME EXAMPLE BANK LIMITED",
            Direction.credit,
            "Inward Remittance",
            "other_income",
        ),
        (
            "QuickRemit 000000000009 to A/C 900000000001 - Jane Sample With Transaction Ref#MQXX1",
            DEBIT,
            "Remittance",
            "family",
        ),
        ("Cardless Cash withdrawal 1000000000000000000000001 TESTATM1", DEBIT, "ATM Withdrawal", "other"),
        ("WALLET TOP UP EXAMPLEPAY", DEBIT, "Wallet Top-up", "transfer"),
        ("Account to Account Transfer FROM ACCOUNT No:019000000001 TO", DEBIT, "Transfer", "transfer"),
    ],
)
def test_account_statement_vocabulary(raw, direction, name, slug):
    assert normalize_merchant(raw, direction) == name
    assert guess_category_slug(raw, direction) == slug


def test_interest_charges_stay_fees_and_credit_interest_is_income():
    assert guess_category_slug("INTEREST CHARGE", DEBIT) == "fees"
    assert guess_category_slug("INTEREST", Direction.credit) == "other_income"

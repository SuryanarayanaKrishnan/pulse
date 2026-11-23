from uuid import uuid4

import pytest

from app.models.transaction import Direction
from app.services.categorisation import CategoryResolver
from app.services.merchants import clean_descriptor, guess_category_name, merchant_key, normalize_merchant

DEBIT, CREDIT = Direction.debit, Direction.credit


@pytest.mark.parametrize(
    ("raw", "name", "category"),
    [
        ("CARREFOUR MOE DUBAI AE", "Carrefour", "Groceries"),
        ("MAF CARREFOUR HYPERMARKET 0123", "Carrefour", "Groceries"),
        ("POS-LULU HYPERMARKET AL BARSHA 1234", "Lulu", "Groceries"),
        ("TALABAT DUBAI ARE", "Talabat", "Dining"),
        ("DELIVEROO.AE", "Deliveroo", "Dining"),
        ("CAREEM *RIDE DUBAI", "Careem", "Transport"),
        ("CAREEM FOOD", "Careem Food", "Dining"),
        ("UBER *EATS", "Uber Eats", "Dining"),
        ("PAYFORT*NOON.COM", "Noon", "Shopping"),
        ("NOON FOOD", "Noon Food", "Dining"),
        ("AMAZON.AE", "Amazon.ae", "Shopping"),
        ("AMZN MKTP AE", "Amazon.ae", "Shopping"),
        ("ENOC STN 123 DUBAI", "ENOC", "Fuel"),
        ("ADNOC DISTRIBUTION", "ADNOC", "Fuel"),
        ("DEWA 98765", "DEWA", "Utilities"),
        ("SEWA BILL PAYMENT", "SEWA", "Utilities"),
        ("ETISALAT", "e&", "Utilities"),
        ("E& (ETISALAT) DUBAI", "e&", "Utilities"),
        ("DU TELECOM", "du", "Utilities"),
        ("SALIK RECHARGE", "Salik", "Transport"),
        ("RTA NOL TOP UP", "RTA", "Transport"),
        ("TABBY FZ-LLC", "Tabby", "Shopping"),
        ("TAMARA", "Tamara", "Shopping"),
        ("NETFLIX.COM", "Netflix", "Subscriptions"),
        ("EMIRATES AIRLINE", "Emirates Airline", "Travel"),
    ],
)
def test_dictionary_hits(raw, name, category):
    assert normalize_merchant(raw, DEBIT) == name
    assert guess_category_name(raw, DEBIT) == category


def test_unknown_merchant_falls_back_to_cleaned_title_case():
    assert normalize_merchant("Refund Co") == "Refund Co"
    assert normalize_merchant("JOES CAFE DUBAI AE 12345") == "Joes Cafe"
    assert guess_category_name("JOES CAFE") is None
    assert normalize_merchant("") is None and normalize_merchant(None) is None


def test_no_false_positive_on_substrings():
    # "EMIRATES NBD" must not become the airline; "DUBAI" alone must not be "du".
    assert guess_category_name("EMIRATES NBD") is None
    assert guess_category_name("DUBAI COFFEE ROASTERS") is None
    assert guess_category_name("MAXIM'S CAFE") is None


def test_direction_sensitive_entries():
    assert guess_category_name("SALARY JAN", CREDIT) == "Salary"
    assert guess_category_name("PAYMENT RECEIVED - THANK YOU", CREDIT) == "Credit Card Payment"
    assert guess_category_name("ANNUAL FEE", DEBIT) == "Fees & Charges"
    assert guess_category_name("ANNUAL FEE", CREDIT) is None


def test_clean_descriptor_strips_noise():
    assert clean_descriptor("POS PURCHASE  PAYFORT*NOON DUBAI AE") == "NOON"
    assert clean_descriptor("shop  1234") == "SHOP"
    assert merchant_key("  Carrefour  ") == "carrefour"
    assert merchant_key(None) is None


def test_resolver_rules_beat_dictionary():
    groceries, dining, shopping = uuid4(), uuid4(), uuid4()
    system = {"Groceries": groceries, "Dining": dining, "Shopping": shopping}
    plain = CategoryResolver({}, system)
    assert plain.enrich("CARREFOUR MOE", DEBIT).category_id == groceries
    assert plain.enrich("CARREFOUR MOE", DEBIT).source == "dictionary"
    ruled = CategoryResolver({"carrefour": shopping}, system)
    e = ruled.enrich("CARREFOUR MOE", DEBIT)
    assert (e.merchant_normalized, e.category_id, e.source) == ("Carrefour", shopping, "rule")
    unknown = ruled.enrich("JOES CAFE", DEBIT)
    assert unknown.category_id is None and unknown.source is None
    # rule for an otherwise-unknown merchant
    assert CategoryResolver({"joes cafe": dining}, system).enrich("JOES CAFE", DEBIT).category_id == dining

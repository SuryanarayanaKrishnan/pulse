from datetime import UTC, date, datetime

import pytest

from app.models.transaction import Direction
from app.services.institutions import institutions
from app.services.parsers import registry
from app.services.parsers.dates import parse_date, to_dubai_date
from app.services.parsers.sms import ALL

DEBIT, CREDIT = Direction.debit, Direction.credit

# (sender, text, institution, amount_minor, direction, merchant, last4, date)
CASES = [
    (
        "ENBD",
        "Purchase of AED 45.00 with Credit Card ending 1234 at CARREFOUR, DUBAI. Avl Cr. limit is AED 20,000.00",
        "enbd",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        None,
    ),
    (
        "EmiratesNBD",
        "Your Debit Card ending 1234 was used for AED 1,045.50 at TALABAT on 12-Mar-26. Avl bal AED 100",
        "enbd",
        104550,
        DEBIT,
        "TALABAT",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "ENBD",
        "AED 5,000.00 has been credited to your account ending 5678.",
        "enbd",
        500000,
        CREDIT,
        None,
        "5678",
        None,
    ),
    (
        "ADCB",
        "Your Card XXX1234 was used for AED 45.00 at CARREFOUR on 12/03/2026. Avl. limit AED 5,000",
        "adcb",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "ADCB",
        "ADCB: AED 1,000.00 credited to your account XXX5678",
        "adcb",
        100000,
        CREDIT,
        None,
        "5678",
        None,
    ),
    (
        "BankFAB",
        "Your Credit Card ending 1234 was used for a purchase of AED 45.00 at CARREFOUR on 12-Mar-26",
        "fab",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "FAB",
        "FAB: Your account ending 5678 has been credited with AED 1,000.00",
        "fab",
        100000,
        CREDIT,
        None,
        "5678",
        None,
    ),
    (
        "MASHREQ",
        "AED 45.00 has been spent on your Mashreq Credit Card ending 1234 at CARREFOUR on 12-Mar-2026.",
        "mashreq",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "RAKBANK",
        "Your RAKBANK Credit Card XXXX1234 has been charged AED 45.00 at CARREFOUR on 12/03/2026.",
        "rakbank",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "RAKBANK",
        "Your a/c XXXX5678 has been debited with AED 300.00 for ATM",
        "rakbank",
        30000,
        DEBIT,
        "ATM",
        "5678",
        None,
    ),
    (
        "DIB",
        "Dear Customer, your Card No. XXXX1234 was used for AED 45.00 at CARREFOUR on 12/03/2026. Available balance AED 1",
        "dib",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "ADIB",
        "Your Cr. Card XXX1234 was used for AED 45.00 at NOON.COM on 12-Mar-26.",
        "adib",
        4500,
        DEBIT,
        "NOON.COM",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "ADIB",
        "Purchase transaction of AED 45.00 on card ending 1234 at CARREFOUR was successful",
        "adib",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        None,
    ),
    (
        "HSBC",
        "HSBC: AED 45.00 was spent on your card ending 1234 at CARREFOUR.",
        "hsbc",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        None,
    ),
    (
        "Citibank",
        "Citibank: Your Citi Credit Card ending 1234 was charged AED 45.00 at CARREFOUR on 12/03/26.",
        "citi",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        date(2026, 3, 12),
    ),
    (
        "Citi",
        "Citi Alert: A payment of AED 500.00 was credited to your card ending 1234.",
        "citi",
        50000,
        CREDIT,
        None,
        "1234",
        None,
    ),
    (
        "CBD",
        "CBD: Transaction of AED 45.00 done on your Credit Card ending 1234 at CARREFOUR.",
        "cbd",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        None,
    ),
    (
        "EmiratesIslamic",
        "A purchase of AED 45.00 was made on your Emirates Islamic Credit Card ending 1234 at CARREFOUR.",
        "eib",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        None,
    ),
    (
        "LIV",
        "Liv. You spent AED 45.00 at CARREFOUR with card ending 1234.",
        "liv",
        4500,
        DEBIT,
        "CARREFOUR",
        "1234",
        None,
    ),
    ("LIV", "Liv. You received AED 1,000.00 from AHMED.", "liv", 100000, CREDIT, "AHMED", None, None),
    # foreign currency
    (
        "ENBD",
        "Purchase of USD 12.99 with Credit Card ending 1234 at NETFLIX.COM, LOS GATOS.",
        "enbd",
        1299,
        DEBIT,
        "NETFLIX.COM",
        "1234",
        None,
    ),
]


@pytest.mark.parametrize(("sender", "text", "inst", "amount", "direction", "merchant", "last4", "on"), CASES)
def test_bank_sms(sender, text, inst, amount, direction, merchant, last4, on):
    r = registry.parse(text, sender=sender, received_at=datetime(2026, 3, 12, 9, 0, tzinfo=UTC))
    assert r is not None, text
    assert (r.institution, r.amount_minor, r.direction, r.merchant_raw, r.last4) == (
        inst,
        amount,
        direction,
        merchant,
        last4,
    )
    assert r.currency == ("USD" if "USD" in text else "AED")
    assert r.posted_at is not None
    assert to_dubai_date(r.posted_at) == (on or date(2026, 3, 12))


def test_every_bank_has_a_parser_and_resolves_by_sender():
    assert {p.institution for p in ALL} == set(institutions.keys)
    for inst in institutions:
        for sender in inst.sms_senders:
            assert registry.resolve(None, sender, "x").institution == inst.key  # type: ignore[union-attr]
    assert set(registry.institutions) == set(institutions.keys)


def test_institution_hint_accepts_free_text_names():
    assert registry.resolve("Emirates NBD", None, "x").institution == "enbd"  # type: ignore[union-attr]
    assert registry.resolve("Abu Dhabi Islamic Bank", None, "x").institution == "adib"  # type: ignore[union-attr]
    assert registry.resolve("unknown bank", None, "x").institution == "generic"  # type: ignore[union-attr]


def test_text_sniffing_without_sender():
    r = registry.parse(
        "Your Emirates Islamic Credit Card ending 1234: a purchase of AED 9.00 was made on your Emirates Islamic Credit Card ending 1234 at TALABAT."
    )
    assert r is not None and r.institution == "eib"


def test_otp_and_non_transactions_are_ignored_for_every_bank():
    for p in ALL:
        assert p.parse("Your OTP is 123456. Do not share. Purchase of AED 45.00 card ending 1234") is None
        assert p.parse("Dear customer, visit our branch for new offers.") is None
    assert registry.parse("ENBD alert: your OTP 889911 for AED 50 purchase", sender="ENBD") is None


def test_bank_parser_falls_back_to_generic_heuristics_but_keeps_institution():
    # Wording no template knows, but the loose heuristics read it.
    r = registry.parse("Salary credited Dhs. 15000 to your account ending 9876", sender="MASHREQ")
    assert r is not None and r.direction is CREDIT and r.amount_minor == 1_500_000
    assert r.institution == "mashreq"


def test_generic_fallback_reads_common_wording_without_sender():
    r = registry.parse("Your Cr. Card XXX1234 was used for AED 45.00 at TALABAT DUBAI. Avl limit AED 100")
    assert r is not None and (r.amount_minor, r.merchant_raw, r.last4, r.institution) == (
        4500,
        "TALABAT DUBAI",
        "1234",
        None,
    )


@pytest.mark.parametrize(
    ("s", "d"),
    [
        ("12/03/2026", date(2026, 3, 12)),
        ("12-03-26", date(2026, 3, 12)),
        ("12 Mar 2026", date(2026, 3, 12)),
        ("12-Mar-26", date(2026, 3, 12)),
        ("12MAR26", date(2026, 3, 12)),
        ("12 March, 2026", date(2026, 3, 12)),
        ("2026-03-12", date(2026, 3, 12)),
        ("31/02/2026", None),
        ("nonsense", None),
    ],
)
def test_parse_date(s, d):
    assert parse_date(s) == d


# Wordings taken from real UAE bank alert emails (names, card numbers and amounts replaced).
EMAIL_ALERTS = [
    (
        "MashreqAlerts@mashreq.com",
        "Dear Customer, Thank you for banking with Mashreq Bank. Please note the details of a recent "
        "transaction on your Mashreq Card. Your Mashreq Cashback Card ending with 1234 was used for a "
        "purchase of AED 42.00 at EXAMPLE SHOP SHARJAH AE on 03-OCT-2026 05:08 PM. Available limit is "
        "AED 32,556.00 This is a system generated alert.",
        "mashreq",
        4200,
        DEBIT,
        "EXAMPLE SHOP SHARJAH AE",
        "1234",
        date(2026, 10, 3),
    ),
    (
        "alert@emiratesnbd.com",
        "Emirates NBD Purchase amount has been refunded to your card account CIF:XXX00XXX Dear Customer, "
        "Amount of AED 38.67 from NOON has been credited to your card ending 1234. Available Limit is "
        "AED 18,638.42",
        "enbd",
        3867,
        CREDIT,
        "NOON",
        "1234",
        None,
    ),
    (
        "CardPayment@emiratesnbd.com",
        "Emirates NBD Your Credit Card payment CIF: XXX00XXX Dear CUSTOMER, Payment of AED 3609.00 towards "
        "your Credit Card ending 1234 on 16/11/2026 was received through Online Banking. Your available "
        "limit is AED 19,247.56",
        "enbd",
        360900,
        CREDIT,
        None,
        "1234",
        date(2026, 11, 16),
    ),
    (
        "donotreply@crm.bankfab.com",
        "Ref: Card activity on your Dubai First Credit Card Dear Customer, Please see the below details of "
        "your recent Dubai First Credit Card transaction. Card Transaction Card ending with:\xa01234 "
        "Amount:\xa0AED\xa010.00 At:\xa0EXAMPLE RESTAURANT Date:\xa011-09-2026 Available Balance: "
        "AED\xa04,990.00 If you do not recognize this transaction, please contact us immediately",
        "fab",
        1000,
        DEBIT,
        "EXAMPLE RESTAURANT",
        "1234",
        date(2026, 9, 11),
    ),
]


@pytest.mark.parametrize(
    ("sender", "text", "inst", "amount", "direction", "merchant", "last4", "on"), EMAIL_ALERTS
)
def test_bank_alert_emails(sender, text, inst, amount, direction, merchant, last4, on):
    r = registry.parse(text, sender=sender)
    assert r is not None
    assert (r.institution, r.amount_minor, r.direction, r.merchant_raw, r.last4) == (
        inst,
        amount,
        direction,
        merchant,
        last4,
    )
    assert r.confidence >= 0.85  # read by the bank template, not the generic fallback
    assert (to_dubai_date(r.posted_at) if r.posted_at and on else None) == on


def test_dubai_first_alert_without_amount_is_not_guessed():
    # Foreign-currency alerts sometimes arrive with the amount blank; never invent one.
    text = (
        "Ref: Card activity on your Dubai First Credit Card Card Transaction Card ending with:\xa01234 "
        "Amount:\xa0USD\xa0 At:\xa0Example Lounge Date:\xa015-10-2026 Available Balance: AED\xa05,000.00"
    )
    assert registry.parse(text, sender="donotreply@crm.bankfab.com") is None

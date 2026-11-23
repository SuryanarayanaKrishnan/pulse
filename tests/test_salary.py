"""Salary detection and the recurring-salary summary (pure)."""

from datetime import date

import pytest

from app.models.transaction import Direction
from app.services.salary import Payment, detect_salary, summarise

CR, DR = Direction.credit, Direction.debit


@pytest.mark.parametrize(
    ("text", "marker", "employer"),
    [
        ("SALARY CREDIT FROM ACME TRADING LLC", "salary", "Acme Trading LLC"),
        ("WPS SALARY - ACME LLC", "wps", "Acme LLC"),
        ("WPS PAYMENT ABC CONTRACTING FZE 0098321", "wps", "ABC Contracting FZE"),
        ("Wage Protection System credit Emirates Group", "wps", "Emirates Group"),
        ("PAYROLL MARCH 2026 FROM EMIRATES GROUP", "payroll", "Emirates Group"),
        ("PAYROLL: Globex Corp", "payroll", "Globex Corp"),
        ("SAL CR ACME LLC 25/03/2026", "salary", "Acme LLC"),
        ("SALARY FEB 2026", "salary", None),
        ("SALARY TRANSFER REF 123456789", "salary", None),
        ("salary", "salary", None),
    ],
)
def test_detects_salary_markers_and_employer(text, marker, employer):
    m = detect_salary(text, CR, 1_500_000)
    assert m is not None and m.matched_by == marker and m.employer == employer


@pytest.mark.parametrize(
    "text",
    [
        "SALARY ADVANCE FROM ACME",
        "SALARY LOAN DISBURSEMENT",
        "SALARY ACCOUNT FEE REFUND",
        "REVERSAL OF SALARY",
        "SALARY CERTIFICATE CHARGE",
        "CARREFOUR MOE DUBAI",
        "PAYMENT RECEIVED - THANK YOU",
        "TRANSFER FROM AHMED",
        "",
    ],
)
def test_ignores_non_salary_credits(text):
    assert detect_salary(text, CR, 1_500_000) is None


def test_only_credits_of_plausible_size():
    assert detect_salary("SALARY ACME LLC", DR, 1_500_000) is None
    assert detect_salary("SALARY ACME LLC", None, 1_500_000) is None
    assert detect_salary("SALARY ACME LLC", CR, 49_999) is None  # under AED 500
    assert detect_salary("SALARY ACME LLC", CR, 50_000) is not None


def test_sms_text_is_used_when_the_descriptor_is_empty():
    sms = "Salary credited Dhs. 15000 to your account ending 9876"
    m = detect_salary(None, CR, 1_500_000, sms)
    assert m is not None and m.matched_by == "salary" and m.employer is None  # no "from": never guess
    sms2 = "AED 15,000.00 salary credited to account ending 9876 from ACME LLC on 25-Mar-2026. Avl bal AED 20,000"
    m2 = detect_salary(None, CR, 1_500_000, sms2)
    assert m2 is not None and m2.employer == "Acme LLC"
    assert detect_salary(None, CR, 1_500_000, "AED 15,000 credited to your account") is None
    assert detect_salary(None, DR, 1_500_000, sms) is None


def pay(y, m, d, amount=1_500_000, employer="Acme LLC", currency="AED"):
    return Payment(date(y, m, d), amount, employer, currency)


def test_summary_empty():
    s = summarise([], date(2026, 4, 1))
    assert s.currency is None and not s.is_recurring and s.average_monthly_minor is None and s.monthly == []


def test_single_payment_is_detected_but_not_recurring():
    s = summarise([pay(2026, 3, 25)], date(2026, 3, 26))
    assert s.months_observed == 1 and s.is_recurring is False and s.next_expected_on is None
    assert s.average_monthly_minor == 1_500_000 and s.typical_day == 25 and s.previous_month_minor is None


def test_recurring_summary_average_day_and_next_expected():
    payments = [
        pay(2026, 1, 25, 1_400_000),
        pay(2026, 2, 26, 1_500_000),
        pay(2026, 3, 25, 1_600_000),
        pay(2026, 4, 24, 1_800_000),
    ]
    s = summarise(payments, date(2026, 4, 25))
    assert s.is_recurring and s.months_observed == 4 and s.currency == "AED"
    assert s.average_monthly_minor == round((1_800_000 + 1_600_000 + 1_500_000) / 3)  # last three months
    assert s.typical_day == 25
    assert s.next_expected_on == date(2026, 5, 25)
    assert (s.latest_month_minor, s.previous_month_minor) == (1_800_000, 1_600_000)
    assert [m for m, _ in s.monthly][:2] == [date(2026, 4, 1), date(2026, 3, 1)]


def test_next_expected_clamps_day_and_goes_stale():
    s = summarise([pay(2026, 1, 31), pay(2026, 2, 28), pay(2026, 3, 31)], date(2026, 4, 5))
    assert s.typical_day == 31 and s.next_expected_on == date(2026, 4, 30)
    stale = summarise([pay(2025, 1, 25), pay(2025, 2, 25)], date(2026, 6, 1))
    assert stale.is_recurring and stale.next_expected_on is None  # stream seems to have stopped


def test_two_payments_in_a_month_are_summed_and_employers_are_listed():
    payments = [
        pay(2026, 3, 25, 1_000_000, "Acme LLC"),
        pay(2026, 3, 28, 300_000, "Side Gig FZ"),
        pay(2026, 2, 25, 1_000_000, "Acme LLC"),
    ]
    s = summarise(payments, date(2026, 3, 30))
    assert dict(s.monthly)[date(2026, 3, 1)] == 1_300_000
    assert s.typical_day == 25  # main employer = most payments
    assert {e for e, _, _ in s.employers} == {"Acme LLC", "Side Gig FZ"}


def test_dominant_currency_only():
    s = summarise(
        [pay(2026, 1, 25), pay(2026, 2, 25), pay(2026, 2, 26, 99, currency="USD")], date(2026, 3, 1)
    )
    assert s.currency == "AED" and s.months_observed == 2

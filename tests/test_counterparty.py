"""Person-to-person transfer detection (synthetic descriptions)."""

import pytest

from app.models.transaction import Direction
from app.services.parsers.counterparty import Counterparty, extract_counterparty, is_holder

HOLDER = "TESTA EXAMPLE-HOLDER"
D, C = Direction.debit, Direction.credit


@pytest.mark.parametrize(
    ("text", "direction", "expected"),
    [
        (
            "IPP TRANSFER 000000000000AE450260001234567890123 - JANE Q SAMPLE - /REF/ Dinner split - T_0a1b2c3d4e5f6a7b",
            D,
            Counterparty("JANE Q SAMPLE", "outgoing", None, "0123"),
        ),
        (
            "IPP TRANSFER 000000000000AE890350001234567890124 - BOB T EXAMPLE - /REF/ Rent share",
            C,
            Counterparty("BOB T EXAMPLE", "incoming", None, "0124"),
        ),
        (
            "QuickRemit 000000000009 to A/C 900000000001 - Jane Q Sample With Transaction Ref#MQXX00000000001",
            D,
            Counterparty("Jane Q Sample", "outgoing", None, "0001"),
        ),
        (
            "Online Local Fund Transfer, Sam Example, A/C No: 000000000042 EXAMPLE BANK",
            D,
            Counterparty("Sam Example", "outgoing", None, "0042"),
        ),
        (
            "Inward Remittance SAMPLE SENDER NAME EXAMPLE BANK MIDDLE EAST LIMITED",
            C,
            Counterparty("SAMPLE SENDER NAME", "incoming"),
        ),
        (
            "AANI TRANSFER TO Jane Sample 0501234567 /REF/ lunch",
            D,
            Counterparty("Jane Sample", "outgoing", "+971501234567", None),
        ),
        (
            "Aani transfer from Bob Example +971 55 123 4567",
            C,
            Counterparty("Bob Example", "incoming", "+971551234567", None),
        ),
        (
            "IPP TRANSFER AE450260001234567890123 - Sam Example - /REF/ x",
            D,
            Counterparty("Sam Example", "outgoing", None, "0123"),
        ),
    ],
)
def test_person_to_person(text, direction, expected):
    assert extract_counterparty(text, direction, HOLDER) == expected


@pytest.mark.parametrize(
    "text",
    [
        "IPP TRANSFER 000000000000400000XXXXXX0001 - Testa - /REF/ Credit card payment - T_00ab",
        "IPP TRANSFER 000000000000AE070330000019000000002 - TESTA EXAMPLE-HOLDER - /REF/ Own Account Transfer",
        "IPP TRANSFER 000000000000AE070330000019000000002 - Testa Example - /REF/ rent",  # truncated own name
        "Online Local Fund Transfer, Testa, A/C No: 000000000001 EXAMPLE BANK, CARD PAYMENT",
        "Account to Account Transfer FROM ACCOUNT No:019000000001 TO ACCOUNT No: 019000000002",
        "Cardless Cash withdrawal 1000000000000000000000001 TESTATM1",
        "Salary Received",
        "POS PURCHASE CARREFOUR DUBAI AE",
        "",
        "Transfer fee 5.25 0501234567",  # phone but no name
    ],
)
def test_not_person_to_person(text):
    assert extract_counterparty(text, D, HOLDER) is None


def test_holder_matching_tolerates_truncation_but_not_other_people():
    assert is_holder("Testa", HOLDER) and is_holder("TESTA EXAMPLE-HOLDER", HOLDER)
    assert is_holder("Testa Example-Hol", HOLDER)
    assert not is_holder("Jane Sample", HOLDER) and not is_holder("Testa", None)


def test_without_a_holder_name_own_transfers_still_skip_by_wording():
    assert (
        extract_counterparty("IPP TRANSFER 000000000000AE07033000001900000000 - X - /REF/ Own Account", D)
        is None
    )

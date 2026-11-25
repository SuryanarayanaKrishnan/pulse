from app.models.transaction import Direction
from app.services.parsers import ParserRegistry, registry
from app.services.parsers.base import ParsedTransaction, Parser
from app.services.parsers.generic import GenericParser


def test_generic_debit():
    r = registry.parse("Purchase of AED 1,234.50 at CARREFOUR MOE on card ending 4321. Avl bal AED 10")
    assert r is not None
    assert r.amount_minor == 123_450
    assert r.currency == "AED"
    assert r.direction is Direction.debit
    assert r.merchant_raw == "CARREFOUR MOE"
    assert r.last4 == "4321"


def test_generic_credit_and_dhs():
    r = GenericParser().parse("Salary credited Dhs. 15000 to your account ending 9876")
    assert r is not None and r.direction is Direction.credit
    assert r.amount_minor == 1_500_000 and r.currency == "AED" and r.last4 == "9876"


def test_otp_and_non_transaction_ignored():
    assert registry.parse("Your OTP is 123456 for AED 50 payment") is None
    assert registry.parse("Hello from your bank") is None


def test_registry_prefers_hint_then_match_then_fallback():
    class Fake:
        institution = "FakeBank"

        def matches(self, sender: str | None, text: str) -> bool:
            return sender == "FAKEBANK"

        def parse(self, text: str, *, received_at=None) -> ParsedTransaction | None:
            return ParsedTransaction(1, "AED", Direction.debit, institution=self.institution)

    assert isinstance(Fake(), Parser)
    reg = ParserRegistry(fallback=GenericParser())
    reg.register(Fake())
    assert reg.resolve("fakebank", None, "x").institution == "FakeBank"  # type: ignore[union-attr]
    assert reg.resolve(None, "FAKEBANK", "x").institution == "FakeBank"  # type: ignore[union-attr]
    assert reg.resolve(None, "OTHER", "x").institution == "generic"  # type: ignore[union-attr]
    assert reg.institutions == ["fakebank"]


def test_tabby_card_due_notice():
    from datetime import UTC, date, datetime

    from app.services.parsers.notices import parse_tabby_card_due

    text = (
        "Your Tabby Card payment is due in 9 days 24 Oct — 23 Nov · Due on 3 December "
        "A no-fee minimum amount of   2,341.75 will be automatically deducted on due date. "
        "251.59 Minimum amount With a 94.07 fee next month Pay 2,341.75 No-fee minimum amount "
        "Pay 2,390.13 Statement amount"
    )
    n = parse_tabby_card_due(text, received_at=datetime(2026, 11, 24, 9, 14, tzinfo=UTC))
    assert n is not None
    assert (n.due_date, n.min_due_minor, n.statement_minor) == (date(2026, 12, 3), 234_175, 239_013)
    # Year rolls over when the due day has already passed this calendar year.
    late = parse_tabby_card_due(
        text.replace("3 December", "5 January"), received_at=datetime(2026, 12, 28, tzinfo=UTC)
    )
    assert late is not None and late.due_date == date(2027, 1, 5)
    assert (
        parse_tabby_card_due("Your noon order is confirmed", received_at=datetime(2026, 11, 24, tzinfo=UTC))
        is None
    )

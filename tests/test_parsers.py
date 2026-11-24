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

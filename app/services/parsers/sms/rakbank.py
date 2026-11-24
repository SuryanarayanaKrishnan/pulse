"""RAKBANK SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "rakbank",
    [
        # "Your RAKBANK Credit Card XXXX1234 has been charged AED 45.00 at CARREFOUR on 12/03/2026."
        T(r"rakbank (?:credit|debit) card {L4} has been charged {AMT} at {M}", DR),
        # "Your a/c XXXX5678 has been debited with AED 45.00" is covered by the common templates.
        T(r"{AMT} has been credited in your (?:a/c|account) {L4}", CR),
    ],
    name_hints=["RAKBANK", "RAK Bank"],
)

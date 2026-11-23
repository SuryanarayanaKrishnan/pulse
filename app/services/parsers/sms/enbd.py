"""Emirates NBD SMS. Illustrative wordings; adjust to real messages as they are collected."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "enbd",
    [
        # "Purchase of AED 45.00 with Credit Card ending 1234 at CARREFOUR, DUBAI. Avl Cr. limit is AED 20,000.00"
        T(r"purchase of {AMT} with (?:credit|debit) card ending {L4} at {M}", DR),
        # "Your Debit Card ending 1234 was used for AED 45.00 at CARREFOUR on 12-Mar-26."
        T(r"(?:credit|debit) card ending {L4} was used for {AMT} at {M}", DR),
        # "AED 5,000.00 has been credited to your account ending 5678."
        T(r"{AMT} has been credited to your account ending {L4}", CR),
    ],
    name_hints=["Emirates NBD", "ENBD"],
)

"""HSBC UAE SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "hsbc",
    [
        # "HSBC: Card ending 1234 was used for AED 45.00 at CARREFOUR on 12MAR26."
        T(r"card ending {L4} was used for {AMT} at {M}", DR),
        # "HSBC: AED 45.00 was spent on your card ending 1234 at CARREFOUR."
        T(r"{AMT} was spent on your card ending {L4} at {M}", DR),
        # "HSBC: Your account ending 5678 was credited with AED 1,000.00."
        T(r"account ending {L4} was credited with {AMT}", CR),
    ],
    name_hints=["HSBC"],
)

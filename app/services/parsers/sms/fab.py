"""First Abu Dhabi Bank SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "fab",
    [
        # "Your Credit Card ending 1234 was used for a purchase of AED 45.00 at CARREFOUR on 12-Mar-26"
        T(r"card ending {L4} was used for a purchase of {AMT} at {M}", DR),
        # "FAB: Debit Card ending 1234: AED 45.00 spent at CARREFOUR"
        T(r"card ending {L4}: {AMT} spent at {M}", DR),
        # "FAB: Your account ending 5678 has been credited with AED 1,000.00"
        T(r"account ending {L4} has been credited with {AMT}", CR),
    ],
    name_hints=["FAB", "First Abu Dhabi Bank"],
)

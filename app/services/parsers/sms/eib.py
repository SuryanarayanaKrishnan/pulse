"""Emirates Islamic SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "eib",
    [
        # "A purchase of AED 45.00 was made on your Emirates Islamic Credit Card ending 1234 at CARREFOUR."
        T(
            r"a purchase of {AMT} was made on your emirates islamic (?:credit|debit) card ending {L4} at {M}",
            DR,
        ),
        # "Emirates Islamic: AED 1,000.00 has been credited to your account ending 5678."
        T(r"{AMT} has been credited to your account ending {L4}", CR),
    ],
    name_hints=["Emirates Islamic"],
)

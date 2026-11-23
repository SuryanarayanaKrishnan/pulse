"""Commercial Bank of Dubai SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "cbd",
    [
        # "CBD: Transaction of AED 45.00 done on your Credit Card ending 1234 at CARREFOUR."
        T(r"transaction of {AMT} done on your (?:credit|debit) card ending {L4} at {M}", DR),
        # "CBD: AED 1,000.00 credited to your account ending 5678."
        T(r"{AMT} credited to your account ending {L4}", CR),
    ],
    name_hints=["CBD", "Commercial Bank of Dubai"],
)

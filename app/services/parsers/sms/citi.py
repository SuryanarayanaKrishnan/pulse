"""Citi UAE SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "citi",
    [
        # "Citibank: Your Citi Credit Card ending 1234 was charged AED 45.00 at CARREFOUR on 12/03/26."
        T(r"citi (?:credit |debit )?card ending {L4} was charged {AMT} at {M}", DR),
        # "Citi Alert: A payment of AED 500.00 was credited to your card ending 1234."
        T(r"payment of {AMT} was credited to your card ending {L4}", CR),
    ],
    name_hints=["Citibank", "Citi Alert"],
)

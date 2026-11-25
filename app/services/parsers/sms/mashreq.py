"""Mashreq SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "mashreq",
    [
        # Alert email: "Your Mashreq Cashback Card ending with 1234 was used for a purchase of AED 42.00
        # at EXAMPLE SHOP SHARJAH AE on 03-OCT-2026 05:08 PM. Available limit is AED 32,556.00"
        T(r"card ending(?: with)? {L4} was used for a purchase of {AMT} at {M} on {DATE}", DR),
        # "AED 45.00 has been spent on your Mashreq Credit Card ending 1234 at CARREFOUR on 12-Mar-2026."
        T(r"{AMT} has been spent on your mashreq (?:credit|debit) card ending {L4} at {M}", DR),
        # "AED 45.00 spent using Mashreq Debit Card XXXX1234 at CARREFOUR"
        T(r"{AMT} spent using mashreq (?:credit|debit) card {L4} at {M}", DR),
        # "Mashreq: AED 1,000.00 is credited to your account XXXX5678"
        T(r"{AMT} is credited to your account {L4}", CR),
    ],
    name_hints=["Mashreq"],
)

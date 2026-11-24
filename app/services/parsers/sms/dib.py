"""Dubai Islamic Bank SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "dib",
    [
        # "Dear Customer, your Card No. XXXX1234 was used for AED 45.00 at CARREFOUR on 12/03/2026."
        T(r"card no\.? {L4} was used for {AMT} at {M}", DR),
        # "Dear Customer, your account XXXX5678 has been credited with AED 1,000.00"
        T(r"account {L4} has been credited with {AMT}", CR),
    ],
    name_hints=["Dubai Islamic Bank", "DIB"],
)

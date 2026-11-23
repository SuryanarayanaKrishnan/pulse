"""Liv (Emirates NBD digital bank) SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "liv",
    [
        # "Liv. You spent AED 45.00 at CARREFOUR with card ending 1234."
        T(r"you spent {AMT} at {M} with (?:your )?(?:\w+ )?card ending {L4}", DR),
        # "Liv. You received AED 1,000.00 from AHMED."
        T(r"you received {AMT} from {M}", CR),
    ],
    name_hints=["Liv."],
)

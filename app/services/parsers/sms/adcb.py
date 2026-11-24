"""ADCB SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "adcb",
    [
        # "Your Card XXX1234 was used for AED 45.00 at CARREFOUR on 12/03/2026. Avl. limit AED ..."
        T(r"card {L4} was used for {AMT} at {M}", DR),
        # "ADCB: Purchase AED 45.00 at CARREFOUR using Card XXX1234"
        T(r"purchase {AMT} at {M} using card {L4}", DR),
        # "ADCB: AED 1,000.00 credited to your account XXX5678"
        T(r"{AMT} credited to your account {L4}", CR),
    ],
    name_hints=["ADCB"],
)

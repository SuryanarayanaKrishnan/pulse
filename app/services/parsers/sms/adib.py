"""Abu Dhabi Islamic Bank SMS."""

from app.services.parsers.sms.engine import CR, DR, SmsParser, T

PARSER = SmsParser(
    "adib",
    [
        # "Your Cr. Card XXX1234 was used for AED 45.00 at CARREFOUR on 12-Mar-26." (also in COMMON)
        T(r"cr\. card {L4} was used for {AMT} at {M}", DR),
        # "ADIB: Purchase transaction of AED 45.00 on card ending 1234 at CARREFOUR was successful"
        T(r"purchase transaction of {AMT} on card ending {L4} at {M} was successful", DR),
        # "ADIB: AED 1,000.00 deposited into your account XXX5678"
        T(r"{AMT} deposited (?:in|into) your account {L4}", CR),
    ],
    name_hints=["ADIB", "Abu Dhabi Islamic Bank"],
)

"""Phone numbers: E.164 normalisation (UAE-aware) and the hash used for contact matching.

Hash contract (the Flutter app must do the same on-device): normalise to E.164 *with* the leading ``+``
(e.g. ``+971501234567``), UTF-8 encode, SHA-256, lower-case hex. The server stores only this hash for
matching. Phone numbers have very little entropy, so the hash stops casual reading of a database dump
and of request logs, not a determined brute force; it is a convenience, not an anonymity guarantee.
"""

import hashlib
import re

UAE_CC = "971"
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_UAE_MOBILE = re.compile(r"^\+9715[024568]\d{7}$")
_UAE_LANDLINE = re.compile(r"^\+971[2-4679]\d{7}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class InvalidPhoneError(ValueError):
    pass


def normalise_phone(raw: str) -> str:
    """Return E.164 or raise InvalidPhoneError.

    UAE conveniences: ``050 123 4567`` / ``0501234567`` / ``501234567`` / ``971501234567`` /
    ``00971501234567`` / ``+971 (0) 50 123 4567`` all become ``+971501234567``. Numbers outside the UAE
    must already carry a country code (``+44...`` or ``0044...``): a bare national number is ambiguous.
    """
    s = (raw or "").strip()
    if not s or re.search(r"[A-Za-z]", s) or re.search(r"[^\d\s()+\-.]", s):
        raise InvalidPhoneError("Not a phone number")
    if "+" in s[1:]:
        raise InvalidPhoneError("Not a phone number")
    digits = re.sub(r"\D", "", s)
    international = s.startswith("+")
    if not international and digits.startswith("00"):
        digits, international = digits[2:], True
    if international:
        if digits.startswith(UAE_CC + "0"):  # "+971 (0) 50 ..." - the national trunk 0 is not dialled
            digits = UAE_CC + digits[len(UAE_CC) + 1 :]
    elif digits.startswith(UAE_CC) and len(digits) == 12:
        pass  # 971501234567
    elif digits.startswith("0") and len(digits) in (9, 10):
        digits = UAE_CC + digits[1:]  # 0501234567 / 04 123 4567
    elif len(digits) == 9 and digits.startswith("5"):
        digits = UAE_CC + digits  # 501234567
    else:
        raise InvalidPhoneError("Use international format (+<country code>...) for non-UAE numbers")
    e164 = "+" + digits
    if not _E164.match(e164):
        raise InvalidPhoneError("Not a valid phone number")
    if e164.startswith("+" + UAE_CC) and not (_UAE_MOBILE.match(e164) or _UAE_LANDLINE.match(e164)):
        raise InvalidPhoneError("Not a valid UAE phone number")
    return e164


def is_uae_mobile(e164: str) -> bool:
    return bool(_UAE_MOBILE.match(e164))


def phone_hash(e164: str) -> str:
    return hashlib.sha256(e164.encode()).hexdigest()


def is_phone_hash(value: str) -> bool:
    return bool(_SHA256_HEX.match(value))

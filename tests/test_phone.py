import hashlib

import pytest

from app.services.phone import InvalidPhoneError, is_phone_hash, is_uae_mobile, normalise_phone, phone_hash


@pytest.mark.parametrize(
    "raw",
    ["0501234567", "050 123 4567", "050-123-4567", "501234567", "971501234567", "+971501234567",
     "00971501234567", "+971 (0) 50 123 4567", " +971.50.123.4567 ", "(050) 123 4567"],
)  # fmt: skip
def test_uae_mobile_variants(raw):
    assert normalise_phone(raw) == "+971501234567"


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("04 123 4567", "+97141234567"),
        ("+442071234567", "+442071234567"),
        ("0044 20 7123 4567", "+442071234567"),
    ],
)
def test_landline_and_international(raw, want):
    assert normalise_phone(raw) == want


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "05012345",
        "0501234567890",
        "+971 12345678",
        "+9715",
        "2071234567",
        "050 123 4567 ext 2",
        "++971501234567",
        "+0123456789",
    ],
)
def test_invalid(raw):
    with pytest.raises(InvalidPhoneError):
        normalise_phone(raw)


def test_hash_contract():
    h = phone_hash("+971501234567")
    assert h == hashlib.sha256(b"+971501234567").hexdigest() and is_phone_hash(h)
    assert not is_phone_hash(h.upper()) and not is_phone_hash("abc")
    assert is_uae_mobile("+971501234567") and not is_uae_mobile("+97141234567")

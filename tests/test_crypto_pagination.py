from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.crypto import EncryptionError, Encryptor, generate_key
from app.core.errors import AppError
from app.services.pagination import decode_cursor, encode_cursor


def test_encrypt_roundtrip_and_rotation():
    k1, k2 = generate_key(), generate_key()
    old = Encryptor([k1]).encrypt("s3cret")
    assert old != "s3cret"
    rotated_enc = Encryptor([k2, k1])
    assert rotated_enc.decrypt(old) == "s3cret"
    new = rotated_enc.rotate(old)
    assert Encryptor([k2]).decrypt(new) == "s3cret"


def test_encryptor_errors():
    with pytest.raises(EncryptionError):
        Encryptor([])
    with pytest.raises(EncryptionError):
        Encryptor(["not-a-key"])
    with pytest.raises(EncryptionError):
        Encryptor([generate_key()]).decrypt("garbage")
    enc = Encryptor([generate_key()])
    with pytest.raises(EncryptionError):
        Encryptor([generate_key()]).decrypt(enc.encrypt("x"))


def test_cursor_roundtrip():
    ts, id_ = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), uuid4()
    assert decode_cursor(encode_cursor(ts, id_)) == (ts, id_)


def test_bad_cursor():
    with pytest.raises(AppError):
        decode_cursor("not-a-cursor")

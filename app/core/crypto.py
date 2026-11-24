"""Fernet helpers for tokens and passwords stored at rest (rotation via MultiFernet)."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class EncryptionError(RuntimeError):
    pass


class Encryptor:
    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise EncryptionError("FERNET_KEYS is not configured")
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])
        except ValueError as exc:
            raise EncryptionError("Invalid Fernet key in FERNET_KEYS") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise EncryptionError("Could not decrypt value (wrong key or corrupted data)") from exc

    def rotate(self, token: str) -> str:
        """Re-encrypt a token with the primary (first) key."""
        return self._fernet.rotate(token.encode()).decode()


def generate_key() -> str:
    return Fernet.generate_key().decode()


def get_encryptor() -> Encryptor:
    return Encryptor(get_settings().fernet_keys)

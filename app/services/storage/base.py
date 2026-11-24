"""File storage abstraction: local disk for development, S3-compatible (Supabase Storage) for production."""

from typing import Protocol


class StorageError(RuntimeError):
    pass


class FileNotFoundInStorage(StorageError):
    pass


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    async def get(self, key: str) -> bytes:
        """Raise FileNotFoundInStorage when the key does not exist."""
        ...

    async def delete(self, key: str) -> None:
        """Idempotent: deleting a missing key is not an error."""
        ...


def validate_key(key: str) -> str:
    """Keys are relative, slash-separated, and may not escape their root."""
    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or any(p in ("", ".", "..") for p in parts)
        or "\\" in key
        or "\0" in key
    ):
        raise StorageError(f"Invalid storage key: {key!r}")
    return key

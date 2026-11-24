from functools import lru_cache

from app.core.config import Settings, get_settings
from app.services.storage.base import FileNotFoundInStorage, Storage, StorageError
from app.services.storage.local import LocalStorage
from app.services.storage.s3 import S3Storage


def build_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage.from_settings(
            endpoint_url=settings.s3_endpoint_url,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
        )
    return LocalStorage(settings.storage_local_dir)


@lru_cache
def _cached() -> Storage:
    return build_storage(get_settings())


def get_storage() -> Storage:
    """FastAPI dependency / worker helper; override in tests."""
    return _cached()


__all__ = [
    "FileNotFoundInStorage",
    "LocalStorage",
    "S3Storage",
    "Storage",
    "StorageError",
    "build_storage",
    "get_storage",
]

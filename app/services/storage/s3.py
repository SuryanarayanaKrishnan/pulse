"""S3-compatible storage (Supabase Storage S3 endpoint or any S3). boto3 is sync: calls run in a thread."""

import asyncio
from typing import Any

from app.services.storage.base import FileNotFoundInStorage, StorageError, validate_key


class S3Storage:
    def __init__(self, client: Any, bucket: str, prefix: str = "") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    @classmethod
    def from_settings(
        cls, *, endpoint_url: str, bucket: str, region: str, access_key_id: str, secret_access_key: str
    ) -> "S3Storage":
        if not (endpoint_url and access_key_id and secret_access_key):
            raise StorageError("S3 storage requires S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY")
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3}
            ),
        )
        return cls(client, bucket)

    def _key(self, key: str) -> str:
        validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=self._key(key),
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                resp = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404", "NotFound"):
                    raise FileNotFoundInStorage(key) from exc
                raise StorageError(f"S3 get failed: {exc}") from exc
            body: bytes = resp["Body"].read()
            return body

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))

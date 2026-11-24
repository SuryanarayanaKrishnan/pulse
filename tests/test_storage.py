import pytest

from app.core.config import Settings
from app.services.storage import LocalStorage, S3Storage, StorageError, build_storage
from app.services.storage.base import FileNotFoundInStorage


async def test_local_roundtrip_and_delete(tmp_path):
    st = LocalStorage(tmp_path)
    await st.put("statements/u1/a.pdf", b"hello", "application/pdf")
    assert await st.get("statements/u1/a.pdf") == b"hello"
    await st.delete("statements/u1/a.pdf")
    await st.delete("statements/u1/a.pdf")  # idempotent
    with pytest.raises(FileNotFoundInStorage):
        await st.get("statements/u1/a.pdf")


@pytest.mark.parametrize("key", ["../x", "a/../../x", "/abs", "", "a//b", "a\\b", "a/./b"])
async def test_local_rejects_bad_keys(tmp_path, key):
    with pytest.raises(StorageError):
        await LocalStorage(tmp_path).put(key, b"x")


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            err = Exception("missing")
            err.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
            raise err
        return {"Body": _Body(self.objects[(Bucket, Key)][0])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


async def test_s3_roundtrip_with_prefix():
    client = FakeS3Client()
    st = S3Storage(client, "statements", prefix="dev/")
    await st.put("u/a.pdf", b"data", "application/pdf")
    assert ("statements", "dev/u/a.pdf") in client.objects
    assert client.objects[("statements", "dev/u/a.pdf")][1] == "application/pdf"
    assert await st.get("u/a.pdf") == b"data"
    await st.delete("u/a.pdf")
    with pytest.raises(FileNotFoundInStorage):
        await st.get("u/a.pdf")
    with pytest.raises(StorageError):
        await st.put("../x", b"")


def test_build_storage_selects_backend(tmp_path):
    assert isinstance(
        build_storage(Settings(storage_backend="local", storage_local_dir=str(tmp_path))), LocalStorage
    )
    with pytest.raises(StorageError):
        build_storage(Settings(storage_backend="s3"))
    s3 = build_storage(
        Settings(
            storage_backend="s3",
            s3_endpoint_url="https://x.supabase.co/storage/v1/s3",
            s3_access_key_id="k",
            s3_secret_access_key="s",
        )
    )
    assert isinstance(s3, S3Storage)

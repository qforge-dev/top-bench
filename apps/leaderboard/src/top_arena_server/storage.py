from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import boto3

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

from .config import Settings


@runtime_checkable
class ObjectStorage(Protocol):
    async def put(self, key: str, value: bytes, *, content_type: str = "audio/wav") -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...


class FileObjectStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        destination = (self._root / key).resolve()
        if not destination.is_relative_to(self._root):
            msg = f"object key escapes storage root: {key}"
            raise ValueError(msg)
        return destination

    async def put(self, key: str, value: bytes, *, content_type: str = "audio/wav") -> None:
        del content_type
        destination = self._path(key)

        def write() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(f"{destination.suffix}.part")
            temporary.write_bytes(value)
            temporary.replace(destination)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)


class S3ObjectStorage:
    def __init__(self, *, bucket: str, prefix: str, region: str) -> None:
        if not bucket:
            msg = "TOP_ARENA_S3_BUCKET is required when storage_backend=s3"
            raise ValueError(msg)
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client: S3Client = boto3.client("s3", region_name=region)

    def _key(self, key: str) -> str:
        return "/".join(part for part in (self._prefix, key.lstrip("/")) if part)

    async def put(self, key: str, value: bytes, *, content_type: str = "audio/wav") -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=self._key(key),
            Body=value,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(
            self._client.get_object,
            Bucket=self._bucket,
            Key=self._key(key),
        )
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket,
                Key=self._key(key),
            )
        except self._client.exceptions.ClientError as error:
            response = error.response
            if response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket,
            Key=self._key(key),
        )


def create_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend == "s3":
        return S3ObjectStorage(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            region=settings.s3_region,
        )
    return FileObjectStorage(settings.storage_path)

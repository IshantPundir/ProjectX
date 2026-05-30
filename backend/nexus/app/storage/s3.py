"""S3-compatible `ObjectStorage` implementation (boto3).

Works against any S3-protocol provider — Cloudflare R2, AWS S3, Supabase
Storage, MinIO, Backblaze B2 — by varying only the constructor args
(endpoint, region, credentials, path-style). There is deliberately ONE
implementation: all of our candidate providers speak S3, so "switch
provider" is a config change, not a new class. A genuinely non-S3 backend
(e.g. GCS native) would add a sibling class and a branch in the factory.

boto3 is synchronous; network calls are offloaded with ``asyncio.to_thread``
so they never block the event loop. Presigned-URL generation is a local
signing operation (no I/O) and runs inline.
"""
from __future__ import annotations

import asyncio

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.storage.base import ObjectMeta


class S3CompatibleStorage:
    """ObjectStorage backed by an S3-compatible provider."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        force_path_style: bool,
    ) -> None:
        self._bucket = bucket
        # Empty string → None so boto3 falls back to AWS regional endpoints.
        self._endpoint_url = endpoint_url or None
        self._region = region or None
        self._access_key_id = access_key_id or None
        self._secret_access_key = secret_access_key or None
        self._force_path_style = force_path_style

    def _client(self):
        """Build a fresh boto3 S3 client. Overridden via patch in tests."""
        cfg = Config(
            # SigV4 is required by R2 and is the modern default for S3.
            signature_version="s3v4",
            s3={"addressing_style": "path" if self._force_path_style else "auto"},
        )
        return boto3.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            config=cfg,
        )

    async def presign_get_url(self, key: str, *, ttl_seconds: int) -> str:
        # Local signing only — no network call, safe to run inline.
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )

    async def head(self, key: str) -> ObjectMeta | None:
        client = self._client()
        try:
            resp = await asyncio.to_thread(
                client.head_object, Bucket=self._bucket, Key=key
            )
        except ClientError:
            # 404 / 403 / NoSuchKey all mean "not retrievable" for our purposes.
            return None
        return ObjectMeta(
            key=key,
            size_bytes=int(resp.get("ContentLength", 0)),
            content_type=resp.get("ContentType"),
        )

    async def download_to_path(self, key: str, dest_path: str) -> None:
        client = self._client()
        await asyncio.to_thread(client.download_file, self._bucket, key, dest_path)

    async def upload_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        client = self._client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

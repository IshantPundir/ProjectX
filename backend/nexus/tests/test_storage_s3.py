"""Unit tests for the S3-compatible object-storage client.

These never touch the network — the boto3 client is mocked at the
`_client()` factory seam (the same seam tests use for resume_service).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from app.storage.base import ObjectMeta
from app.storage.s3 import S3CompatibleStorage


def _storage(**overrides) -> S3CompatibleStorage:
    kwargs = dict(
        bucket="interview-sessions",
        region="auto",
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        access_key_id="AKIA_TEST",
        secret_access_key="secret_test",
        force_path_style=True,
    )
    kwargs.update(overrides)
    return S3CompatibleStorage(**kwargs)


@pytest.mark.asyncio
async def test_presign_get_url_uses_bucket_key_and_ttl(monkeypatch):
    storage = _storage()
    fake = MagicMock()
    fake.generate_presigned_url.return_value = "https://signed.example/obj?sig=x"
    monkeypatch.setattr(storage, "_client", lambda: fake)

    url = await storage.presign_get_url("recordings/t/s.mp4", ttl_seconds=900)

    assert url == "https://signed.example/obj?sig=x"
    fake.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "interview-sessions", "Key": "recordings/t/s.mp4"},
        ExpiresIn=900,
    )


@pytest.mark.asyncio
async def test_head_returns_meta_on_success(monkeypatch):
    storage = _storage()
    fake = MagicMock()
    fake.head_object.return_value = {"ContentLength": 1234, "ContentType": "video/mp4"}
    monkeypatch.setattr(storage, "_client", lambda: fake)

    meta = await storage.head("recordings/t/s.mp4")

    assert meta == ObjectMeta(
        key="recordings/t/s.mp4", size_bytes=1234, content_type="video/mp4"
    )


@pytest.mark.asyncio
async def test_head_returns_none_when_object_missing(monkeypatch):
    storage = _storage()
    fake = MagicMock()
    fake.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
    )
    monkeypatch.setattr(storage, "_client", lambda: fake)

    assert await storage.head("recordings/t/missing.mp4") is None


def test_force_path_style_sets_path_addressing():
    """Non-AWS providers (R2/Supabase/MinIO) require path-style addressing."""
    storage = _storage(force_path_style=True)
    client = storage._client()
    assert client.meta.config.s3["addressing_style"] == "path"


def test_empty_endpoint_falls_back_to_aws_regional():
    """Empty endpoint → boto3 uses AWS regional endpoints (endpoint_url=None)."""
    storage = _storage(endpoint_url="", region="us-east-1")
    assert storage._endpoint_url is None

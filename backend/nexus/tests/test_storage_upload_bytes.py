from unittest.mock import MagicMock, patch

import pytest

from app.storage.s3 import S3CompatibleStorage


def _storage() -> S3CompatibleStorage:
    return S3CompatibleStorage(
        bucket="rec-bucket", region="auto", endpoint_url="https://r2.example.com",
        access_key_id="k", secret_access_key="s", force_path_style=True,
    )


@pytest.mark.asyncio
async def test_upload_bytes_calls_put_object():
    fake_client = MagicMock()
    storage = _storage()
    with patch.object(storage, "_client", return_value=fake_client):
        await storage.upload_bytes("thumbs/t/s/q1.webp", b"RIFFdata", content_type="image/webp")
    fake_client.put_object.assert_called_once_with(
        Bucket="rec-bucket",
        Key="thumbs/t/s/q1.webp",
        Body=b"RIFFdata",
        ContentType="image/webp",
    )

from unittest.mock import MagicMock

import pytest

from app.storage.s3 import S3CompatibleStorage


@pytest.mark.asyncio
async def test_download_to_path_calls_boto_download_file(tmp_path, monkeypatch):
    store = S3CompatibleStorage(
        bucket="rec", region="auto", endpoint_url="https://r2.example",
        access_key_id="k", secret_access_key="s", force_path_style=False,
    )
    fake_client = MagicMock()
    monkeypatch.setattr(store, "_client", lambda: fake_client)
    dest = tmp_path / "v.mp4"
    await store.download_to_path("sessions/abc/recording.mp4", str(dest))
    fake_client.download_file.assert_called_once_with(
        "rec", "sessions/abc/recording.mp4", str(dest)
    )

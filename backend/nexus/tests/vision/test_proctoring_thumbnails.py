from unittest.mock import AsyncMock

import pytest

from app.modules.vision.service import attach_flag_thumbnails


class _Thumb:
    def __init__(self, ref_id, key):
        self.kind = "flag"; self.ref_id = ref_id; self.s3_key = key


@pytest.mark.asyncio
async def test_attaches_url_to_matching_flag(monkeypatch):
    flagged = [{"kind": "off_screen_sustained", "start_ms": 5000, "end_ms": 6000,
                "confidence": 0.65}]
    thumbs = [_Thumb("5000", "thumbs/t/s/flag_5000.webp")]
    fake_storage = type("S", (), {"presign_get_url": AsyncMock(return_value="https://signed/f")})()
    import app.modules.vision.service as svc
    monkeypatch.setattr(svc, "get_object_storage", lambda: fake_storage)

    out = await attach_flag_thumbnails(flagged, thumbs)
    assert out[0]["thumbnail_url"] == "https://signed/f"


@pytest.mark.asyncio
async def test_unmatched_flag_has_no_url(monkeypatch):
    flagged = [{"kind": "down_glance", "start_ms": 100, "end_ms": 200, "confidence": 0.6}]
    out = await attach_flag_thumbnails(flagged, [])
    assert "thumbnail_url" not in out[0] or out[0]["thumbnail_url"] is None

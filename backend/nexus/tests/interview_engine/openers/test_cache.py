"""Unit tests for build_opener_cache."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.openers.library import OpenerLibrary
from app.modules.interview_engine.openers.cache import (
    BuildReport, build_opener_cache,
)


class _FakeAudioFrame:
    """Minimal stand-in for livekit.rtc.AudioFrame."""
    def __init__(self, data: bytes):
        self.data = data


class _FakeTTSStream:
    """Stand-in for the TTS stream returned by tts.synthesize()."""
    def __init__(self, frames: list):
        self._frames = frames

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for f in self._frames:
            yield MagicMock(frame=f)


@pytest.mark.asyncio
async def test_build_opener_cache_populates_every_variant():
    """Every variant in the library has audio_frames populated after
    a successful build."""
    lib = OpenerLibrary()

    # Mock TTS.synthesize to return a small frame list per call.
    mock_tts = MagicMock()
    mock_tts.synthesize = MagicMock(
        return_value=_FakeTTSStream([_FakeAudioFrame(b"x")])
    )

    report = await build_opener_cache(library=lib, tts=mock_tts)

    assert isinstance(report, BuildReport)
    assert report.failed_variants == []
    # Every (kind, sub_ctx) -> variants list -> each variant must have
    # audio_frames populated.
    for variants in lib._vocabulary.values():
        for v in variants:
            assert v.audio_frames is not None
            assert len(v.audio_frames) >= 1


@pytest.mark.asyncio
async def test_build_opener_cache_tolerates_partial_failure():
    """If TTS.synthesize raises for some calls, the build continues for
    the rest. Failed variants are recorded in the report; their
    audio_frames stay None."""
    lib = OpenerLibrary()

    call_count = {"n": 0}
    def synthesize_with_intermittent_failure(text):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated TTS failure")
        return _FakeTTSStream([_FakeAudioFrame(b"x")])

    mock_tts = MagicMock()
    mock_tts.synthesize = synthesize_with_intermittent_failure

    report = await build_opener_cache(library=lib, tts=mock_tts)

    assert len(report.failed_variants) == 1
    # Other variants succeeded.
    populated = sum(
        1 for variants in lib._vocabulary.values()
        for v in variants if v.audio_frames is not None
    )
    assert populated >= 1

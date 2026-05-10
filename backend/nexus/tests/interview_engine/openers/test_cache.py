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


# ---------------------------------------------------------------------------
# _synthesize_variant retry-with-backoff tests (Bug C)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_variant_retries_on_oserror():
    """Bug C — DNS failures (httpcore.ConnectError, OSError subclass)
    should be retried with exponential backoff, not surfaced after the
    first attempt."""
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts = []

    class FlakeyTTS:
        def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise OSError("[Errno -5] No address associated with hostname")
            return _FakeTTSStream([_FakeAudioFrame(b"ok")])

    variant = OpenerVariant(text="hello")
    _, exc = await _synthesize_variant(variant, FlakeyTTS())
    assert exc is None
    assert variant.audio_frames is not None
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_synthesize_variant_retries_on_timeout():
    """asyncio.TimeoutError is also retryable."""
    import asyncio as _asyncio
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts = []

    class TimeoutThenSuccessTTS:
        def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise _asyncio.TimeoutError("synthesis timeout")
            return _FakeTTSStream([_FakeAudioFrame(b"ok")])

    variant = OpenerVariant(text="hello")
    _, exc = await _synthesize_variant(variant, TimeoutThenSuccessTTS())
    assert exc is None
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_synthesize_variant_does_not_retry_on_non_transient_error():
    """4xx-style errors (BadRequest, auth) MUST NOT be retried."""
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts = []

    class FatalTTS:
        def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            raise ValueError("invalid voice 'foo'")

    variant = OpenerVariant(text="hello")
    _, exc = await _synthesize_variant(variant, FatalTTS())
    assert exc is not None
    assert isinstance(exc, ValueError)
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_synthesize_variant_exhausts_retries_then_returns_last_error():
    """All 3 attempts fail → returns the last error after the bounded budget."""
    from app.modules.interview_engine.openers.library import OpenerVariant
    from app.modules.interview_engine.openers.cache import _synthesize_variant

    attempts = []

    class AlwaysFailTTS:
        def synthesize(self, text):
            attempts.append(len(attempts) + 1)
            raise OSError("permanent network outage")

    variant = OpenerVariant(text="hello")
    _, exc = await _synthesize_variant(variant, AlwaysFailTTS())
    assert exc is not None
    assert isinstance(exc, OSError)
    assert len(attempts) == 3

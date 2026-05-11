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


# ---------------------------------------------------------------------------
# synth_one helper tests (Task 8 — Phase 3 prep)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synth_one_returns_audio_frames_on_success():
    """synth_one is the per-text helper used by both build_opener_cache
    and the engine entrypoint's per-session intro synthesis."""
    from app.modules.interview_engine.openers import synth_one

    class GoodTTS:
        def synthesize(self, text):
            return _FakeTTSStream([_FakeAudioFrame(b"a"), _FakeAudioFrame(b"b")])

    frames = await synth_one(text="Hi, I'm Sam.", tts=GoodTTS())
    assert frames is not None
    assert len(frames) == 2


@pytest.mark.asyncio
async def test_synth_one_returns_none_on_permanent_failure():
    """When all retries exhausted, synth_one returns None (caller
    falls back to text-only TTS)."""
    from app.modules.interview_engine.openers import synth_one

    class AlwaysFailTTS:
        def synthesize(self, text):
            raise OSError("permanent")

    frames = await synth_one(text="Hi, I'm Sam.", tts=AlwaysFailTTS())
    assert frames is None


# ---------------------------------------------------------------------------
# Bounded-concurrency semaphore tests
# (Sarvam 429 burst regression — 2026-05-11)
# ---------------------------------------------------------------------------

class _ConcurrencyTrackingTTS:
    """Records peak concurrent synthesize() calls.

    Each call enters via the stream's __aenter__, sleeps a fixed window
    to overlap with sibling calls, yields one frame, and exits. The
    tracker dict observes the running in-flight count.
    """

    def __init__(self, *, work_delay_s: float = 0.02) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.total_calls = 0
        self._work_delay_s = work_delay_s

    def synthesize(self, text: str):
        return _ConcurrencyTrackingStream(self, self._work_delay_s)


class _ConcurrencyTrackingStream:
    def __init__(self, tts: _ConcurrencyTrackingTTS, work_delay_s: float) -> None:
        self._tts = tts
        self._work_delay_s = work_delay_s
        self._yielded = False

    async def __aenter__(self):
        self._tts.total_calls += 1
        self._tts.in_flight += 1
        if self._tts.in_flight > self._tts.max_in_flight:
            self._tts.max_in_flight = self._tts.in_flight
        # Hold the slot long enough that the gather sees overlap.
        import asyncio as _asyncio
        await _asyncio.sleep(self._work_delay_s)
        return self

    async def __aexit__(self, *exc_info):
        self._tts.in_flight -= 1
        return False

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        yield MagicMock(frame=_FakeAudioFrame(b"x"))


@pytest.mark.asyncio
async def test_build_opener_cache_caps_concurrency_with_semaphore():
    """The semaphore must hard-cap concurrent synthesize() calls.

    Regression: the unbounded asyncio.gather of 76 calls overwhelmed
    Sarvam's per-second rate limit on 2026-05-11. The fix is a process-
    wide semaphore. This test verifies the cap is honored end-to-end:
    with a Semaphore(3) and a library of 10 variants, the peak in-flight
    count must be exactly 3.
    """
    import asyncio as _asyncio

    lib = OpenerLibrary()
    # Trim the library to exactly 10 variants so the peak observation is
    # deterministic — every variant enters the gather queue before the
    # first one releases its slot.
    from app.modules.interview_engine.openers.library import OpenerVariant
    lib._vocabulary = {
        list(lib._vocabulary.keys())[0]: [
            OpenerVariant(text=f"variant-{i}") for i in range(10)
        ]
    }

    tts = _ConcurrencyTrackingTTS(work_delay_s=0.02)
    sem = _asyncio.Semaphore(3)

    report = await build_opener_cache(library=lib, tts=tts, semaphore=sem)

    assert report.success_count == 10
    assert tts.total_calls == 10
    assert tts.max_in_flight == 3, (
        f"semaphore breached: peaked at {tts.max_in_flight} concurrent calls "
        f"(expected exactly 3)"
    )


@pytest.mark.asyncio
async def test_build_opener_cache_without_semaphore_runs_unbounded():
    """When ``semaphore=None``, the gather runs unbounded (legacy /
    test-only path). All variants fire concurrently."""
    from app.modules.interview_engine.openers.library import OpenerVariant

    lib = OpenerLibrary()
    lib._vocabulary = {
        list(lib._vocabulary.keys())[0]: [
            OpenerVariant(text=f"variant-{i}") for i in range(8)
        ]
    }

    tts = _ConcurrencyTrackingTTS(work_delay_s=0.02)
    report = await build_opener_cache(library=lib, tts=tts, semaphore=None)

    assert report.success_count == 8
    assert tts.max_in_flight == 8, (
        f"expected unbounded gather to peak at 8; got {tts.max_in_flight}"
    )


@pytest.mark.asyncio
async def test_synth_one_acquires_semaphore_when_provided():
    """synth_one runs its entire synthesize-and-retry block under the
    semaphore so concurrent intro-line calls from parallel sessions
    cannot collectively bypass the rate-limit cap."""
    import asyncio as _asyncio

    from app.modules.interview_engine.openers import synth_one

    tts = _ConcurrencyTrackingTTS(work_delay_s=0.02)
    sem = _asyncio.Semaphore(2)

    # Fire 5 intro syntheses concurrently — model 5 sessions starting
    # at once on the same worker process. The semaphore must cap them.
    results = await _asyncio.gather(
        *[synth_one(text=f"hi {i}", tts=tts, semaphore=sem) for i in range(5)]
    )

    assert all(r is not None for r in results)
    assert tts.total_calls == 5
    assert tts.max_in_flight == 2, (
        f"synth_one ignored semaphore: peaked at {tts.max_in_flight} "
        f"(expected exactly 2)"
    )


@pytest.mark.asyncio
async def test_synth_one_without_semaphore_runs_immediately():
    """When ``semaphore=None``, synth_one fires without gating
    (preserves the existing zero-arg call signature for tests)."""
    from app.modules.interview_engine.openers import synth_one

    tts = _ConcurrencyTrackingTTS(work_delay_s=0.01)
    frames = await synth_one(text="hello", tts=tts, semaphore=None)
    assert frames is not None
    assert tts.total_calls == 1

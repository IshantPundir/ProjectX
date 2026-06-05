"""Tests for the Ear's turn-audio buffer + Smart Turn predict bridge (B2).

Five focused tests:
  1. Buffering accumulates across appended frames.
  2. Trim to ≤8s tail: retains only the last 8 * sample_rate samples.
  3. reset() clears the buffer to length 0.
  4. predict() wired (mocked detector): returns the detector's probability
     and was called with the buffered tail.
  5. predict() on empty buffer: returns the documented empty contract
     (prediction=0, probability=0.0) WITHOUT calling the detector.

No I/O, no LiveKit, no ONNX — fully unit-testable via an injected mock.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.interview_engine.ear.smart_turn import TurnAudioBuffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeDetector:
    """Fake Smart Turn detector for unit tests.

    Records the last audio array it received so tests can assert on the
    exact slice forwarded by the buffer.
    """

    def __init__(self, prediction: int = 1, probability: float = 0.87) -> None:
        self._prediction = prediction
        self._probability = probability
        self.call_count: int = 0
        self.last_audio: np.ndarray | None = None

    def predict(self, audio: np.ndarray, sample_rate: int = 16000) -> dict:
        self.call_count += 1
        self.last_audio = audio.copy()
        return {"prediction": self._prediction, "probability": self._probability}


def _make_buffer(
    *,
    sample_rate: int = 16000,
    max_seconds: float = 8.0,
    detector: object | None = None,
) -> TurnAudioBuffer:
    """Convenience factory with injectable detector."""
    return TurnAudioBuffer(
        sample_rate=sample_rate,
        max_seconds=max_seconds,
        detector=detector,
    )


# ---------------------------------------------------------------------------
# Test 1 — Buffering accumulates
# ---------------------------------------------------------------------------

def test_append_accumulates_samples() -> None:
    """Appending several short frames keeps a running sum of samples."""
    buf = _make_buffer(detector=_FakeDetector())

    frames = [
        np.zeros(160, dtype=np.float32),    # 10 ms @ 16 kHz
        np.zeros(320, dtype=np.float32),    # 20 ms
        np.zeros(480, dtype=np.float32),    # 30 ms
    ]
    for f in frames:
        buf.append(f)

    expected = sum(len(f) for f in frames)  # 960
    assert len(buf) == expected, (
        f"Expected {expected} accumulated samples, got {len(buf)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Trims to ≤8s tail (keeps TAIL, not head)
# ---------------------------------------------------------------------------

def test_append_trims_to_max_seconds_keeping_tail() -> None:
    """Buffer is capped at exactly 8 * sample_rate samples; the tail is kept."""
    sample_rate = 16000
    max_seconds = 8.0
    cap = int(max_seconds * sample_rate)  # 128_000

    buf = _make_buffer(sample_rate=sample_rate, max_seconds=max_seconds, detector=_FakeDetector())

    # Build a 10-second block with a distinctive sentinel at the very end.
    total_samples = sample_rate * 10  # 160_000 > cap
    audio_block = np.arange(total_samples, dtype=np.float32)  # 0.0 … 159999.0

    buf.append(audio_block)

    # Buffer must be capped at exactly cap samples.
    assert len(buf) == cap, f"Expected {cap} samples, got {len(buf)}"

    # Must keep the TAIL: the last sample of the retained buffer should equal
    # the last sample of the original block.
    retained = buf.as_array()
    assert retained[-1] == pytest.approx(audio_block[-1]), (
        "Buffer should retain the tail (most recent) samples, not the head."
    )
    # And the first retained sample should be the (total_samples - cap)-th
    # sample of the original block.
    assert retained[0] == pytest.approx(audio_block[total_samples - cap]), (
        "First retained sample should be the oldest surviving tail sample."
    )


# ---------------------------------------------------------------------------
# Test 3 — reset() clears the buffer
# ---------------------------------------------------------------------------

def test_reset_clears_buffer() -> None:
    """After reset(), the buffer has exactly 0 samples."""
    buf = _make_buffer(detector=_FakeDetector())
    buf.append(np.zeros(4800, dtype=np.float32))  # 300 ms
    assert len(buf) > 0, "Pre-condition: buffer should be non-empty before reset."

    buf.reset()

    assert len(buf) == 0, f"Expected 0 samples after reset(), got {len(buf)}"


# ---------------------------------------------------------------------------
# Test 4 — predict() wired with a mocked detector
# ---------------------------------------------------------------------------

def test_predict_wired_with_mocked_detector() -> None:
    """predict() calls the injected detector with the buffered tail and returns its probability."""
    fake = _FakeDetector(prediction=1, probability=0.87)
    buf = _make_buffer(sample_rate=16000, max_seconds=8.0, detector=fake)

    # Append two frames so there is audio in the buffer.
    frame_a = np.full(1600, 0.25, dtype=np.float32)   # 100 ms
    frame_b = np.full(3200, 0.50, dtype=np.float32)   # 200 ms
    buf.append(frame_a)
    buf.append(frame_b)

    result = buf.predict()

    # Returned probability (and/or full dict) must reflect the fake detector's output.
    assert result["probability"] == pytest.approx(0.87), (
        f"Expected probability 0.87, got {result['probability']}"
    )
    assert result["prediction"] == 1

    # Detector must have been called exactly once.
    assert fake.call_count == 1, (
        f"Expected detector.predict to be called once, got {fake.call_count}"
    )

    # Detector must have received the entire buffered tail (4800 samples total,
    # well under the 8-second cap so no trimming occurred).
    expected_count = len(frame_a) + len(frame_b)  # 4800
    assert fake.last_audio is not None
    assert len(fake.last_audio) == expected_count, (
        f"Detector received {len(fake.last_audio)} samples, expected {expected_count}."
    )

    # Verify the content: the concatenated frames should match what was passed.
    expected_audio = np.concatenate([frame_a, frame_b])
    np.testing.assert_array_equal(
        fake.last_audio,
        expected_audio,
        err_msg="Detector received wrong audio content.",
    )


# ---------------------------------------------------------------------------
# Test 5 — predict() on empty buffer
# ---------------------------------------------------------------------------

def test_predict_on_empty_buffer_returns_empty_contract_without_calling_detector() -> None:
    """predict() on an empty buffer returns the zero-contract without calling the detector."""
    fake = _FakeDetector(prediction=1, probability=0.87)
    buf = _make_buffer(detector=fake)

    # Buffer is empty — no frames appended.
    assert len(buf) == 0, "Pre-condition: buffer must be empty."

    result = buf.predict()

    # Empty contract: prediction=0, probability=0.0
    assert result["prediction"] == 0, (
        f"Empty buffer should yield prediction=0, got {result['prediction']}"
    )
    assert result["probability"] == pytest.approx(0.0), (
        f"Empty buffer should yield probability=0.0, got {result['probability']}"
    )

    # Detector must NOT have been called.
    assert fake.call_count == 0, (
        f"Detector should not be called on empty buffer, but was called {fake.call_count} time(s)."
    )

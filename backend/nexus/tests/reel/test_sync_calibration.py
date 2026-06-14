"""Per-session sync calibration — the PURE cross-correlation + stderr parsing.

``measure_offset_correction`` finds the shift δ (ms) that best aligns the
candidate speech envelope (built from STT word times + the static offset) onto
the recording's actual speech envelope (from ffmpeg ``silencedetect``). The final
render offset = static_offset + δ. The ffmpeg shell-out itself is not unit-tested
(integration); only the pure pieces are.
"""
from app.modules.reel.actors import _candidate_speech_intervals
from app.modules.reel.timing import (
    _parse_silencedetect,
    measure_offset_correction,
)


def _shift(intervals, delta):
    """Shift every interval by ``delta`` ms (the recording is the candidate + δ)."""
    return [(a + delta, b + delta) for a, b in intervals]


# A realistic-ish candidate envelope: several speech bursts over ~12s.
_CAND = [
    (1000, 2200),
    (3000, 5100),
    (6000, 6800),
    (8200, 11500),
]


def test_recovers_known_positive_delta():
    rec = _shift(_CAND, 600)
    delta = measure_offset_correction(_CAND, rec, bin_ms=40)
    assert abs(delta - 600) <= 40


def test_recovers_known_negative_delta():
    rec = _shift(_CAND, -480)
    delta = measure_offset_correction(_CAND, rec, bin_ms=40)
    assert abs(delta - (-480)) <= 40


def test_zero_delta_when_already_aligned():
    delta = measure_offset_correction(_CAND, list(_CAND), bin_ms=40)
    assert abs(delta) <= 40


def test_empty_candidate_returns_zero():
    assert measure_offset_correction([], _shift(_CAND, 600)) == 0


def test_empty_recording_returns_zero():
    assert measure_offset_correction(_CAND, []) == 0


def test_no_correlation_returns_zero():
    # Recording speech sits entirely OUTSIDE the reachable lag window from the
    # candidate envelope (50s away with a tiny 8s max_lag) → no overlap at any
    # shift → guard returns 0 (fall back to the static offset).
    rec = _shift(_CAND, 50_000)
    assert measure_offset_correction(_CAND, rec, max_lag_ms=8000, bin_ms=40) == 0


def test_noise_recording_returns_zero():
    # A sparse, uncorrelated recording envelope (one tiny blip) gives a best
    # overlap below the confidence threshold → 0.
    rec = [(40_000, 40_080)]
    assert measure_offset_correction(_CAND, rec, max_lag_ms=8000, bin_ms=40) == 0


def test_delta_clamped_to_max_lag():
    # True shift exceeds max_lag; the best in-window δ must never exceed the bound.
    rec = _shift(_CAND, 6000)
    delta = measure_offset_correction(_CAND, rec, max_lag_ms=2000, bin_ms=40)
    assert -2000 <= delta <= 2000


# --- stderr parsing (pure helper factored out of the ffmpeg shell-out) -------

def test_parse_silencedetect_basic_speech_intervals():
    # silencedetect reports SILENCE windows; the SPEECH intervals are the gaps.
    # Recording total ~10s. Silence [0,1.2] and [4.0,4.5] → speech [1.2,4.0] and
    # [4.5, end].
    stderr = (
        "[silencedetect @ 0x1] silence_start: 0\n"
        "[silencedetect @ 0x1] silence_end: 1.2 | silence_duration: 1.2\n"
        "[silencedetect @ 0x1] silence_start: 4.0\n"
        "[silencedetect @ 0x1] silence_end: 4.5 | silence_duration: 0.5\n"
    )
    speech = _parse_silencedetect(stderr, total_ms=10_000)
    assert speech == [(1200, 4000), (4500, 10_000)]


def test_parse_silencedetect_speech_from_start():
    # No silence_start at 0 → speech begins at 0.
    stderr = (
        "[silencedetect @ 0x1] silence_start: 3.0\n"
        "[silencedetect @ 0x1] silence_end: 3.4 | silence_duration: 0.4\n"
    )
    speech = _parse_silencedetect(stderr, total_ms=8000)
    assert speech == [(0, 3000), (3400, 8000)]


def test_parse_silencedetect_all_silence():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 0\n"
        "[silencedetect @ 0x1] silence_end: 5.0 | silence_duration: 5.0\n"
    )
    speech = _parse_silencedetect(stderr, total_ms=5000)
    assert speech == []


def test_parse_silencedetect_no_silence_at_all():
    speech = _parse_silencedetect("", total_ms=7000)
    assert speech == [(0, 7000)]


# --- candidate envelope builder (pure; in actors.py) -------------------------

def test_candidate_envelope_absolute_word_times_with_offset():
    transcript = [
        {"speaker": "agent", "span": {"start_ms": 0, "end_ms": 900},
         "words": [{"text": "hi", "start_ms": 0, "end_ms": 900}]},
        {"speaker": "candidate", "span": {"start_ms": 1000, "end_ms": 2000},
         "words": [{"text": "I", "start_ms": 0, "end_ms": 200},
                   {"text": "did", "start_ms": 300, "end_ms": 1000}]},
        {"speaker": "candidate", "span": {"start_ms": 5000, "end_ms": 5500},
         "words": [{"text": "yes", "start_ms": 0, "end_ms": 400}]},
    ]
    # offset 100: word abs = span.start + word.rel + 100. Agent turns excluded.
    got = _candidate_speech_intervals(transcript, 100)
    assert got == [(1100, 1300), (1400, 2100), (5100, 5500)]


def test_candidate_envelope_skips_empty_and_malformed_words():
    transcript = [
        {"speaker": "candidate", "span": {"start_ms": 0},
         "words": [{"text": "a", "start_ms": 0, "end_ms": 100},
                   {"text": "bad"}]},  # missing timings → skipped
        {"speaker": "candidate", "span": {}, "words": []},  # no words → nothing
    ]
    assert _candidate_speech_intervals(transcript, 0) == [(0, 100)]


def test_candidate_envelope_empty_transcript():
    assert _candidate_speech_intervals([], 500) == []

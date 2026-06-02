from app.modules.reel.timing import (
    answer_span,
    measure_pipeline_lag,
    speaking_intervals,
    wall_anchor,
)


def _state(t_ms, new):
    return {"kind": "audio.user.state", "t_ms": t_ms, "payload": {"new_state": new}}


def _cap(t_ms, pause):
    return {"kind": "turn.captured", "t_ms": t_ms, "payload": {"pause_before_commit_ms": pause}}


def test_wall_anchor_is_engine_t0_minus_recording_start():
    events = [{"kind": "engine.v2.dispatched", "t_ms": 0, "wall_ms": 1_000_000}]
    # engine started 90ms after recording -> video_ms = t_ms + 90
    assert wall_anchor(events, recording_started_at_ms=999_910) == 90


def test_speaking_intervals_pairs_listening_speaking_transitions():
    events = [
        _state(1000, "speaking"), _state(1500, "listening"),
        _state(3000, "speaking"), _state(4200, "listening"),
    ]
    assert speaking_intervals(events) == [(1000, 1500), (3000, 4200)]


def test_speaking_intervals_ignores_unclosed_trailing_speech():
    events = [_state(1000, "speaking"), _state(1500, "listening"), _state(9000, "speaking")]
    assert speaking_intervals(events) == [(1000, 1500)]


def test_answer_span_uses_vad_bounded_by_prev_commit_and_real_end():
    speaking = [(2000, 4000), (5200, 9000), (12000, 13000)]
    events = [
        _state(2000, "speaking"), _state(4000, "listening"),
        _cap(4500, 500),       # earlier turn's commit -> lower bound (4500)
        _state(5200, "speaking"), _state(9000, "listening"),
        _cap(10000, 900),      # our turn: real_end = 10000-900 = 9100
        _state(12000, "speaking"), _state(13000, "listening"),  # next turn, excluded
    ]
    # only the candidate's own speech (after prev commit, before real_end) is taken
    assert answer_span(events, speaking, commit_t_ms=10000) == (5200, 9000)


def test_measure_pipeline_lag_recovers_a_known_lag():
    # candidate speaks at these t_ms; recording is the same audio EARLIER by `lag`.
    speaking = [(10_000, 14_000), (20_000, 23_000), (30_000, 38_000)]
    anchor, lag = 100, 3_000
    rec = [(a + anchor - lag, b + anchor - lag) for a, b in speaking]
    measured = measure_pipeline_lag(speaking, rec, anchor, max_lag_ms=8_000, bin_ms=40)
    assert abs(measured - lag) <= 40   # within one bin


def test_measure_pipeline_lag_zero_when_no_data():
    assert measure_pipeline_lag([], [], 0) == 0

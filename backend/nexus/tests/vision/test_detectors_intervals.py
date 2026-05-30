# tests/vision/test_detectors_intervals.py
import math

from app.modules.vision.detectors import (
    FrameObservation,
    Interval,
    detect_off_screen_intervals,
    detect_down_glances,
    detect_reading_sweeps,
    detect_multi_face_intervals,
)

TH = dict(zone_yaw_deg=15.0, zone_pitch_deg=12.0, far_off_deg=35.0)


def _o(t, yaw_deg, pitch_deg, faces=1, q=0.9):
    return FrameObservation(
        t_ms=t, faces=faces,
        yaw=None if yaw_deg is None else math.radians(yaw_deg),
        pitch=None if pitch_deg is None else math.radians(pitch_deg),
        quality=q,
    )


def test_off_screen_sustained_flagged_above_min_ms():
    # 0–1000ms centered, 1000–4000ms looking right (off), back centered.
    obs = [_o(t, 2, 2) for t in range(0, 1001, 200)]
    obs += [_o(t, 30, 0) for t in range(1200, 4001, 200)]
    obs += [_o(t, 2, 2) for t in range(4200, 5001, 200)]
    out = detect_off_screen_intervals(obs, (0.0, 0.0), min_ms=2000, thresholds=TH)
    assert len(out) == 1
    assert out[0].start_ms >= 1000 and out[0].end_ms <= 4200
    assert out[0].kind == "off_screen_sustained"


def test_off_screen_brief_not_flagged():
    obs = [_o(t, 2, 2) for t in range(0, 1001, 200)]
    obs += [_o(1200, 30, 0), _o(1400, 30, 0)]  # only ~400ms off
    obs += [_o(t, 2, 2) for t in range(1600, 2601, 200)]
    out = detect_off_screen_intervals(obs, (0.0, 0.0), min_ms=2000, thresholds=TH)
    assert out == []


def test_down_glances_counts_brief_pitch_down():
    obs = []
    t = 0
    for _ in range(3):  # three down-glances of ~600ms each
        obs += [_o(t, 0, 2), _o(t + 200, 0, 2)]
        obs += [_o(t + 400, 0, 30), _o(t + 600, 0, 30), _o(t + 800, 0, 30)]
        t += 1200
    out = detect_down_glances(obs, (0.0, 0.0), min_ms=300, max_ms=4000, thresholds=TH)
    assert len(out) == 3
    assert all(i.kind == "down_glance" for i in out)


def test_reading_sweep_detects_rhythmic_horizontal_reversals():
    # Alternate left/right every 200ms for 4s → many reversals.
    obs = []
    for i in range(20):
        yaw = 25 if i % 2 == 0 else -25
        obs.append(_o(i * 200, yaw, 0))
    out = detect_reading_sweeps(obs, (0.0, 0.0), window_ms=4000, min_reversals=4, thresholds=TH)
    assert len(out) >= 1
    assert out[0].kind == "reading_sweep"


def test_multi_face_intervals_flag_sustained_two_faces():
    obs = [_o(t, 2, 2, faces=1) for t in range(0, 1001, 200)]
    obs += [_o(t, 2, 2, faces=2) for t in range(1200, 3201, 200)]  # ~2s of 2 faces
    obs += [_o(t, 2, 2, faces=1) for t in range(3400, 4001, 200)]
    out = detect_multi_face_intervals(obs, min_ms=1500)
    assert len(out) == 1 and out[0].kind == "multiple_faces"
    assert out[0].max_faces == 2

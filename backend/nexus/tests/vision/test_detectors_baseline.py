# tests/vision/test_detectors_baseline.py
import math

from app.modules.vision.detectors import (
    FrameObservation,
    estimate_baseline,
    classify_zone,
)


def _obs(t_ms, yaw_deg, pitch_deg, faces=1, quality=0.9):
    return FrameObservation(
        t_ms=t_ms,
        faces=faces,
        yaw=math.radians(yaw_deg) if yaw_deg is not None else None,
        pitch=math.radians(pitch_deg) if pitch_deg is not None else None,
        quality=quality,
    )


def test_baseline_is_the_dense_cluster():
    # 8 frames clustered near (yaw=2°, pitch=3°), 2 outliers far away.
    obs = [_obs(i * 200, 2, 3) for i in range(8)]
    obs += [_obs(2000, 40, 30), _obs(2200, -45, 25)]
    by, bp = estimate_baseline(obs)
    assert abs(math.degrees(by) - 2) < 6
    assert abs(math.degrees(bp) - 3) < 6


def test_baseline_ignores_unscorable_frames():
    obs = [_obs(i * 200, 1, 1) for i in range(5)]
    obs += [FrameObservation(t_ms=9999, faces=0, yaw=None, pitch=None, quality=0.0)]
    by, bp = estimate_baseline(obs)
    assert abs(math.degrees(by) - 1) < 5


def test_classify_zone_center_and_deviations():
    base = (0.0, 0.0)
    th = dict(zone_yaw_deg=15.0, zone_pitch_deg=12.0, far_off_deg=35.0)
    assert classify_zone(math.radians(2), math.radians(2), *base, **th) == "center"
    assert classify_zone(math.radians(25), math.radians(0), *base, **th) == "right"
    assert classify_zone(math.radians(-25), math.radians(0), *base, **th) == "left"
    assert classify_zone(math.radians(0), math.radians(25), *base, **th) == "down"
    assert classify_zone(math.radians(0), math.radians(-25), *base, **th) == "up"
    assert classify_zone(math.radians(50), math.radians(40), *base, **th) == "far_off"

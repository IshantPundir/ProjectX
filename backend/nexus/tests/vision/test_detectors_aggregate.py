# tests/vision/test_detectors_aggregate.py
import math

from app.modules.vision.detectors import (
    FrameObservation,
    AnalysisResult,
    analyze_observations,
)

CFG = dict(
    zone_yaw_deg=15.0, zone_pitch_deg=12.0, far_off_deg=35.0,
    off_screen_min_ms=2000, down_glance_min_ms=300, down_glance_max_ms=4000,
    reading_window_ms=4000, reading_min_reversals=4, multi_face_min_ms=1500,
    band_high_off_screen_pct=0.25, band_medium_off_screen_pct=0.10,
    band_high_down_glances=12, max_unscorable_pct=0.6,
)


def _o(t, yaw_deg, pitch_deg, faces=1, q=0.9):
    return FrameObservation(
        t_ms=t, faces=faces,
        yaw=None if yaw_deg is None else math.radians(yaw_deg),
        pitch=None if pitch_deg is None else math.radians(pitch_deg),
        quality=q,
    )


def test_clean_session_is_low_band():
    obs = [_o(t, 1, 1) for t in range(0, 20001, 200)]
    res = analyze_observations(obs, **CFG)
    assert isinstance(res, AnalysisResult)
    assert res.risk_band == "low"
    assert res.detector_summary["off_screen_pct"] < 0.05
    assert res.detector_summary["max_faces"] == 1
    assert len(res.gaze_heatmap["grid"]) == 5 and len(res.gaze_heatmap["grid"][0]) == 5


def test_heavy_off_screen_is_high_band():
    # >25% of the session looking right.
    obs = [_o(t, 1, 1) for t in range(0, 10001, 200)]      # ~10s centered
    obs += [_o(t, 30, 0) for t in range(10200, 16001, 200)]  # ~6s off (>25% of 16s)
    res = analyze_observations(obs, **CFG)
    assert res.risk_band == "high"
    assert res.flagged_intervals  # at least the off-screen interval


def test_all_unscorable_is_insufficient_data():
    obs = [FrameObservation(t_ms=t, faces=0, yaw=None, pitch=None, quality=0.0)
           for t in range(0, 5001, 200)]
    res = analyze_observations(obs, **CFG)
    assert res.risk_band == "insufficient_data"
    assert res.gaze_signal_quality in ("unscorable", "low-light")
    assert res.unscorable_pct > 0.6


def test_transient_multi_face_does_not_force_high_band():
    # Mostly centered + a SINGLE spurious 2-face frame (no sustained interval).
    # The raw peak max_faces is still reported, but a one-frame blip must NOT
    # drive the band to "high" — only a SUSTAINED second face should.
    obs = [_o(t, 1, 1) for t in range(0, 10001, 200)]
    obs[10] = _o(2000, 1, 1, faces=2)  # one isolated frame with 2 faces
    res = analyze_observations(obs, **CFG)
    assert res.detector_summary["max_faces"] == 2
    assert res.detector_summary["multi_face_intervals"] == []  # not sustained
    assert res.risk_band == "low"


def test_sustained_multi_face_bands_high():
    # A SUSTAINED 2-face interval (> multi_face_min_ms) must band "high".
    obs = [_o(t, 1, 1) for t in range(0, 5001, 200)]                  # centered
    obs += [_o(t, 1, 1, faces=2) for t in range(5200, 7501, 200)]    # ~2.3s of 2 faces
    obs += [_o(t, 1, 1) for t in range(7700, 9001, 200)]
    res = analyze_observations(obs, **CFG)
    assert len(res.detector_summary["multi_face_intervals"]) >= 1
    assert res.risk_band == "high"

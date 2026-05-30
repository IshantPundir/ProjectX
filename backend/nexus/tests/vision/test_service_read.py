# tests/vision/test_service_read.py
from app.modules.vision.schemas import ProctoringAnalysisRead


def test_read_schema_absent_default():
    r = ProctoringAnalysisRead(status="absent")
    assert r.status == "absent"
    assert r.risk_band is None
    assert r.flagged_intervals == []


def test_read_schema_full_roundtrip():
    r = ProctoringAnalysisRead(
        status="ready", risk_band="medium",
        detector_summary={"off_screen_pct": 0.12, "max_faces": 1,
                          "down_glance_count": 4, "reading_sweep_intervals": 1,
                          "multi_face_intervals": []},
        gaze_heatmap={"grid": [[0] * 5] * 5, "off_screen_timeline": [0.0]},
        flagged_intervals=[{"start_ms": 1000, "end_ms": 3200, "kind": "off_screen_sustained", "confidence": 0.65}],
        gaze_signal_quality="good", unscorable_pct=0.05,
    )
    assert r.model_dump(mode="json")["risk_band"] == "medium"

from app.modules.vision.analysis import _target_frame_index, select_flag_targets


# --- select_flag_targets ---

def test_selects_top_n_by_severity_then_confidence():
    flags = [
        {"kind": "down_glance", "start_ms": 100, "end_ms": 200, "confidence": 0.6},
        {"kind": "off_screen_sustained", "start_ms": 300, "end_ms": 800, "confidence": 0.65},
        {"kind": "multiple_faces", "start_ms": 900, "end_ms": 1000, "confidence": 0.9},
        {"kind": "down_glance", "start_ms": 1100, "end_ms": 1200, "confidence": 0.6},
    ]
    out = select_flag_targets(flags, top_n=2)
    assert [t["kind"] for t in out] == ["multiple_faces", "off_screen_sustained"]
    assert out[0]["start_ms"] == 900


def test_empty_flags_returns_empty():
    assert select_flag_targets([], top_n=6) == []


def test_caps_at_top_n():
    flags = [{"kind": "down_glance", "start_ms": i, "end_ms": i + 1, "confidence": 0.6}
             for i in range(10)]
    assert len(select_flag_targets(flags, top_n=3)) == 3


# --- _target_frame_index ---

def test_target_frame_index_rounds_to_nearest():
    assert _target_frame_index(500, 10.0, 30) == 5      # 0.5s * 10fps
    assert _target_frame_index(1490, 10.0, 30) == 15     # round(14.9)


def test_target_frame_index_clamps_to_last_frame():
    assert _target_frame_index(9_000_000, 10.0, 30) == 29


def test_target_frame_index_floors_at_zero():
    assert _target_frame_index(-100, 10.0, 30) == 0


def test_target_frame_index_no_frame_count_no_upper_clamp():
    assert _target_frame_index(500, 10.0, 0) == 5

from app.modules.vision.analysis import _target_frame_index, select_flag_targets


# --- select_flag_targets ---

def test_returns_all_flags_under_cap():
    # Every flagged interval (proctoring violation) earns a thumbnail target.
    flags = [
        {"kind": "down_glance", "start_ms": 100, "end_ms": 200, "confidence": 0.6},
        {"kind": "off_screen_sustained", "start_ms": 300, "end_ms": 800, "confidence": 0.65},
        {"kind": "multiple_faces", "start_ms": 900, "end_ms": 1000, "confidence": 0.9},
        {"kind": "down_glance", "start_ms": 1100, "end_ms": 1200, "confidence": 0.6},
    ]
    out = select_flag_targets(flags, max_count=100)
    assert len(out) == 4
    # deterministic ordering: most serious first (severity → confidence → earliest)
    assert [t["kind"] for t in out] == [
        "multiple_faces", "off_screen_sustained", "down_glance", "down_glance",
    ]


def test_empty_flags_returns_empty():
    assert select_flag_targets([], max_count=100) == []


def test_skips_flags_without_start_ms():
    flags = [
        {"kind": "down_glance", "end_ms": 200, "confidence": 0.6},  # no start_ms
        {"kind": "multiple_faces", "start_ms": 900, "end_ms": 1000, "confidence": 0.9},
    ]
    out = select_flag_targets(flags, max_count=100)
    assert [t["kind"] for t in out] == ["multiple_faces"]


def test_caps_at_max_count_keeping_most_serious():
    flags = [{"kind": "down_glance", "start_ms": i, "end_ms": i + 1, "confidence": 0.6}
             for i in range(10)]
    flags.append({"kind": "multiple_faces", "start_ms": 50, "end_ms": 60, "confidence": 0.9})
    out = select_flag_targets(flags, max_count=3)
    assert len(out) == 3
    # the most serious flag survives the cap
    assert out[0]["kind"] == "multiple_faces"


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

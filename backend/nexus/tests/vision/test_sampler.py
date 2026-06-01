# tests/vision/test_sampler.py
import pytest

from app.modules.vision.sampler import (
    build_ffmpeg_cmd,
    build_ffprobe_cmd,
    effective_fps,
    parse_probe_json,
    scaled_dimensions,
)


def test_effective_fps_under_budget_uses_target():
    # 16 min at 2 fps = 1920 frames <= 2000 budget → full target fps.
    assert effective_fps(16 * 60, target_fps=2.0, max_frames=2000) == 2.0


def test_effective_fps_over_budget_degrades_uniformly():
    # 40 min at budget 2000 → 2000/2400s = 0.8333... fps.
    eff = effective_fps(40 * 60, target_fps=2.0, max_frames=2000)
    assert eff == pytest.approx(2000 / 2400)
    assert eff < 2.0


def test_effective_fps_zero_or_unknown_duration_falls_back_to_target():
    assert effective_fps(0.0, target_fps=2.0, max_frames=2000) == 2.0
    assert effective_fps(-1.0, target_fps=2.0, max_frames=2000) == 2.0


def test_scaled_dimensions_downscales_wide_and_forces_even():
    # 1280x720 capped to 960 wide → 960x540 (both even).
    assert scaled_dimensions(1280, 720, 960) == (960, 540)


def test_scaled_dimensions_no_upscale_small_source():
    # 640x480 under cap → unchanged (already even).
    assert scaled_dimensions(640, 480, 960) == (640, 480)


def test_scaled_dimensions_rounds_odd_to_even():
    # 963x721 capped to 960 → width even 960, height even.
    out_w, out_h = scaled_dimensions(963, 721, 960)
    assert out_w % 2 == 0 and out_h % 2 == 0


def test_scaled_dimensions_rejects_nonpositive():
    with pytest.raises(ValueError):
        scaled_dimensions(0, 720, 960)


def test_build_ffprobe_cmd_shape():
    cmd = build_ffprobe_cmd("/tmp/rec.mp4")
    assert cmd[0] == "ffprobe"
    assert "/tmp/rec.mp4" in cmd
    assert "-of" in cmd and "json" in cmd


def test_build_ffmpeg_cmd_has_fps_scale_and_rawvideo():
    cmd = build_ffmpeg_cmd("/tmp/rec.mp4", eff_fps=2.0, out_w=960, out_h=540)
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg"
    assert "fps=2.0" in joined or "fps=2.000000" in joined
    assert "scale=960:540" in joined
    assert "rawvideo" in cmd
    assert "bgr24" in cmd
    assert "pipe:1" in cmd
    assert "-threads" in cmd
    assert cmd[cmd.index("-threads") + 1] == "1"


def test_parse_probe_json_extracts_dims_and_duration():
    raw = (
        '{"streams":[{"width":1280,"height":720}],'
        '"format":{"duration":"840.5"}}'
    )
    w, h, dur = parse_probe_json(raw)
    assert (w, h) == (1280, 720)
    assert dur == pytest.approx(840.5)


def test_parse_probe_json_missing_duration_returns_zero():
    raw = '{"streams":[{"width":1280,"height":720}],"format":{}}'
    w, h, dur = parse_probe_json(raw)
    assert (w, h, dur) == (1280, 720, 0.0)


def test_scaled_dimensions_clamps_tiny_source_to_min_two():
    # 1x1 source → both dims clamped to the minimum even value (2).
    assert scaled_dimensions(1, 1, 960) == (2, 2)


def test_effective_fps_zero_budget_returns_zero():
    # max_frames=0 with positive duration → 0.0 (sample_frames guards this with
    # a ValueError before dividing by it).
    assert effective_fps(600, target_fps=2.0, max_frames=0) == 0.0

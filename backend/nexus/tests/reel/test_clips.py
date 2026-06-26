"""Clip cut arg builder — pure tests (lean nexus image; ffmpeg shelled out)."""
from app.modules.reel.clips import build_cut_cmd


def test_cut_cmd_is_cfr_av_locked_and_output_bounded():
    cmd = build_cut_cmd(
        recording_path="/rec.mp4", out_path="/seg.mp4",
        start_ms=10_000, end_ms=14_000, offset_ms=0,
        pad_ms=150, tail_pad_ms=400,
    )
    assert cmd[0] == "ffmpeg"
    # seek before -i for speed
    assert cmd.index("-ss") < cmd.index("-i")
    # OUTPUT duration bound (-t), not input -to: window = (end-start)+pad+tail
    assert "-to" not in cmd
    t = cmd[cmd.index("-t") + 1]
    # v_start = 10000-150 = 9850; v_end = 14000+400 = 14400; dur = 4.550
    assert t == "4.550"
    ss = cmd[cmd.index("-ss") + 1]
    assert ss == "9.850"
    # constant frame rate lock
    assert "-vsync" in cmd and cmd[cmd.index("-vsync") + 1] == "cfr"
    # 30fps in the video filter
    vf = cmd[cmd.index("-vf") + 1]
    assert "fps=30" in vf
    # audio resampled/aligned to the window so A==V duration
    af = cmd[cmd.index("-af") + 1]
    assert "aresample=async=1:first_pts=0" == af
    # constant pixel format
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[-1] == "/seg.mp4"


def test_cut_cmd_has_no_subtitle_burn():
    cmd = build_cut_cmd(
        recording_path="/rec.mp4", out_path="/seg.mp4",
        start_ms=0, end_ms=2000, offset_ms=0,
    )
    joined = " ".join(cmd)
    assert "subtitles=" not in joined
    assert ".ass" not in joined


def test_cut_cmd_offset_shifts_window_back():
    cmd = build_cut_cmd(
        recording_path="/rec.mp4", out_path="/seg.mp4",
        start_ms=5000, end_ms=6000, offset_ms=1000, pad_ms=0, tail_pad_ms=0,
    )
    # v_start = 5000-1000-0 = 4000 -> 4.000 ; dur = 1.000
    assert cmd[cmd.index("-ss") + 1] == "4.000"
    assert cmd[cmd.index("-t") + 1] == "1.000"


def test_cut_cmd_without_overlay_uses_vf_and_no_extra_input():
    cmd = build_cut_cmd(recording_path="rec.mp4", out_path="o.mp4",
                        start_ms=1000, end_ms=2000, offset_ms=0)
    # exactly one input, classic -vf path, no filter_complex/overlay
    assert cmd.count("-i") == 1
    assert "-vf" in cmd
    assert "-filter_complex" not in cmd
    assert "overlay" not in " ".join(cmd)


def test_cut_cmd_with_overlay_adds_input_and_overlay_filter():
    cmd = build_cut_cmd(recording_path="rec.mp4", out_path="o.mp4",
                        start_ms=1000, end_ms=2000, offset_ms=0,
                        overlay_png="banner.png")
    assert cmd.count("-i") == 2
    assert "banner.png" in cmd
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay" in fc
    # the video map is the filter output, audio comes from the source
    assert "-map" in cmd
    j = " ".join(cmd)
    assert "[v]" in j and "0:a" in j
    assert "-vf" not in cmd   # mutually exclusive with filter_complex here

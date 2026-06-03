"""Render arg builders — pure tests (lean nexus image; ffmpeg shelled out)."""
from app.modules.reel.render import build_card_segment_cmd, build_concat_file


def test_concat_file_lists_abspaths_one_per_line():
    body = build_concat_file(["/a/x.mp4", "/a/y.mp4"])
    assert body == "file '/a/x.mp4'\nfile '/a/y.mp4'\n"


def test_card_segment_cmd_with_narration_maps_image_and_audio():
    cmd = build_card_segment_cmd(
        image_path="/t/card.png", out_path="/t/seg.mp4",
        duration_ms=4200, audio_path="/t/narr.wav",
    )
    assert cmd[0] == "ffmpeg"
    assert "/t/card.png" in cmd and "/t/narr.wav" in cmd
    # explicit duration, normalized to the clip params for stream-copy concat
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4.200"
    assert "libx264" in cmd and "yuv420p" in cmd
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert "-loop" in cmd                       # still image looped for the duration
    assert cmd[-1] == "/t/seg.mp4"


def test_card_segment_cmd_without_narration_uses_silent_audio():
    cmd = build_card_segment_cmd(
        image_path="/t/card.png", out_path="/t/seg.mp4", duration_ms=3000,
    )
    # a silent stereo source so every segment carries an audio stream for concat
    joined = " ".join(cmd)
    assert "anullsrc" in joined
    assert cmd[cmd.index("-t") + 1] == "3.000"

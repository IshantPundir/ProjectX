"""Render arg builders — pure tests (lean nexus image; ffmpeg shelled out)."""
from app.modules.reel.render import (
    _clip_to_video,
    build_card_segment_cmd,
    build_concat_cmd,
    first_point_index,
)


class _KindBeat:
    def __init__(self, kind):
        self.kind = kind


def test_first_point_index_finds_first_point():
    beats = [_KindBeat("point"), _KindBeat("clip"), _KindBeat("point"), _KindBeat("outro")]
    assert first_point_index(beats) == 0


def test_first_point_index_skips_leading_non_points():
    beats = [_KindBeat("clip"), _KindBeat("point"), _KindBeat("outro")]
    assert first_point_index(beats) == 1


def test_first_point_index_none_when_no_point():
    beats = [_KindBeat("clip"), _KindBeat("outro")]
    assert first_point_index(beats) is None


class _Beat:
    def __init__(self, words):
        self.words = words


def _w(text, turn_start_ms, rel_start_ms, rel_end_ms):
    return {"text": text, "turn_start_ms": turn_start_ms,
            "rel_start_ms": rel_start_ms, "rel_end_ms": rel_end_ms}


def test_clip_to_video_maps_session_ms_plus_offset():
    # video_ms = turn_start_ms + rel + offset; returns the cut window only.
    beat = _Beat([_w("a", 5000, 0, 300), _w("b", 5000, 400, 700)])
    start, end = _clip_to_video(beat, offset_ms=90)
    assert start == 5000 + 0 + 90        # first word video start
    assert end == 5000 + 700 + 90        # last word video end


def test_clip_to_video_multi_turn_uses_each_words_own_turn_start():
    # two turns: each word carries its own turn_start_ms -> one contiguous cut
    beat = _Beat([_w("w2", 1000, 2000, 2900), _w("w3", 1000, 3000, 3900),
                  _w("x0", 8000, 0, 800)])
    start, end = _clip_to_video(beat, offset_ms=50)
    assert start == 1000 + 2000 + 50     # first word in turn t-1
    assert end == 8000 + 800 + 50        # last word in turn t-2


def test_concat_cmd_uses_concat_filter_and_reencodes():
    cmd = build_concat_cmd(["/a/x.mp4", "/a/y.mp4"], "/a/out.mp4")
    assert cmd[0] == "ffmpeg"
    # both inputs are mapped
    assert cmd.count("-i") == 2
    assert "/a/x.mp4" in cmd and "/a/y.mp4" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    # re-timing concat FILTER, not the concat demuxer + -c copy
    assert "concat=n=2:v=1:a=1" in fc
    assert "[0:v][0:a][1:v][1:a]" in fc
    assert "-c" not in cmd                       # no `-c copy`
    assert "libx264" in cmd and "aac" in cmd
    assert "-map" in cmd and "[v]" in cmd and "[a]" in cmd
    assert cmd[-1] == "/a/out.mp4"


def test_card_segment_cmd_with_narration_pads_audio_and_caps_duration():
    cmd = build_card_segment_cmd(
        image_path="/t/card.png", out_path="/t/seg.mp4",
        duration_ms=4200, audio_path="/t/narr.wav",
    )
    assert cmd[0] == "ffmpeg"
    assert "/t/card.png" in cmd and "/t/narr.wav" in cmd
    # explicit duration caps the segment; narration padded with silence to A==V
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4.200"
    af = cmd[cmd.index("-af") + 1]
    assert "apad" in af
    # constant frame rate to match the clips
    assert "-vsync" in cmd and cmd[cmd.index("-vsync") + 1] == "cfr"
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
    assert "-af" not in cmd                      # silent source already matches -t
    assert cmd[cmd.index("-t") + 1] == "3.000"
    assert "-vsync" in cmd and cmd[cmd.index("-vsync") + 1] == "cfr"

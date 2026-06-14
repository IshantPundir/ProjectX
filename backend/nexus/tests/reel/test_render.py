"""Render arg builders — pure tests (lean nexus image; ffmpeg shelled out)."""
from app.modules.reel.render import (
    _clip_to_video,
    build_card_segment_cmd,
    build_concat_file,
)


class _Beat:
    def __init__(self, words):
        self.words = words


def _w(text, turn_start_ms, rel_start_ms, rel_end_ms):
    return {"text": text, "turn_start_ms": turn_start_ms,
            "rel_start_ms": rel_start_ms, "rel_end_ms": rel_end_ms}


def test_clip_to_video_maps_session_ms_plus_offset():
    # video_ms = turn_start_ms + rel + offset
    beat = _Beat([_w("a", 5000, 0, 300), _w("b", 5000, 400, 700)])
    start, end, caption_words = _clip_to_video(beat, offset_ms=90)
    assert start == 5000 + 0 + 90        # first word video start
    assert end == 5000 + 700 + 90        # last word video end
    # clean_caption_words sentence-cases the lead word; compare case-insensitively
    assert [c["text"].lower() for c in caption_words] == ["a", "b"]
    assert caption_words[0]["start_ms"] == 5090
    assert caption_words[1]["end_ms"] == 5790


def test_clip_to_video_multi_turn_uses_each_words_own_turn_start():
    # two turns: each word carries its own turn_start_ms -> one contiguous cut
    beat = _Beat([_w("w2", 1000, 2000, 2900), _w("w3", 1000, 3000, 3900),
                  _w("x0", 8000, 0, 800)])
    start, end, _ = _clip_to_video(beat, offset_ms=50)
    assert start == 1000 + 2000 + 50     # first word in turn t-1
    assert end == 8000 + 800 + 50        # last word in turn t-2



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

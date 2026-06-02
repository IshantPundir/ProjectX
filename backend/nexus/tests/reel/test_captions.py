from app.modules.reel.captions import group_caption_lines, build_ass, _ass_ts


def _w(text, start_ms, end_ms):
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms, "confidence": 1.0}


def test_group_caption_lines_splits_by_max_words():
    words = [_w(t, i * 100, i * 100 + 80) for i, t in enumerate("a b c d e f g".split())]
    lines = group_caption_lines(words, max_words=3)
    assert [len(ln) for ln in lines] == [3, 3, 1]
    # each line keeps its words' original timings
    assert lines[0][0]["text"] == "a" and lines[1][0]["text"] == "d"


def test_ass_ts_formats_centiseconds():
    assert _ass_ts(0) == "0:00:00.00"
    assert _ass_ts(1500) == "0:00:01.50"
    assert _ass_ts(3661230) == "1:01:01.23"


def test_build_ass_timings_are_clip_relative():
    # clip starts at session-ms 12000; first word at 12400 -> 0.40s into the clip
    words = [_w("six", 12400, 12720), _w("years", 12800, 13300)]
    ass = build_ass(words, clip_start_ms=12000, max_words=5)
    assert "[Events]" in ass and "PlayResX: 1280" in ass
    assert "Dialogue: 0,0:00:00.40,0:00:01.30,Default,,0,0,0,,six years" in ass


def test_build_ass_empty_words_has_no_dialogue():
    ass = build_ass([], clip_start_ms=0)
    assert "Dialogue:" not in ass

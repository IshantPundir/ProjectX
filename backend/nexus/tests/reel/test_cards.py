"""Card text layout + caption cleanup — pure helper tests (lean nexus image)."""
from app.modules.reel.captions import clean_caption_words
from app.modules.reel.cards import wrap_to_width


def _w(text, start, end):
    return {"text": text, "start_ms": start, "end_ms": end}


def test_caption_drops_nonlexical_fillers():
    words = [_w("i", 0, 100), _w("um", 100, 200), _w("designed", 200, 500),
             _w("uh", 500, 600), _w("it", 600, 800)]
    out = [w["text"] for w in clean_caption_words(words)]
    assert out == ["I", "designed", "it"]


def test_caption_collapses_adjacent_stutter():
    words = [_w("will", 0, 100), _w("will", 100, 200), _w("will", 200, 300),
             _w("it", 300, 400)]
    out = [w["text"] for w in clean_caption_words(words)]
    assert out == ["Will", "it"]


def test_caption_sentence_cases_first_word_and_standalone_i():
    words = [_w("so", 0, 100), _w("i", 100, 200), _w("ran", 200, 300)]
    out = [w["text"] for w in clean_caption_words(words)]
    assert out == ["So", "I", "ran"]


def test_caption_keeps_meaningful_words_and_timing():
    words = [_w("like", 0, 150), _w("python", 150, 500)]
    out = clean_caption_words(words)
    assert [w["text"] for w in out] == ["Like", "python"]   # 'like' kept (not a filler)
    assert out[1]["start_ms"] == 150 and out[1]["end_ms"] == 500


def _measure(s):
    return len(s)   # 1px per char — deterministic stand-in for font.getlength


def test_greedy_wraps_words_to_fit_width():
    assert wrap_to_width("a bb ccc", 5, _measure) == ["a bb", "ccc"]


def test_no_wrap_when_everything_fits():
    assert wrap_to_width("a b c", 99, _measure) == ["a b c"]


def test_single_overlong_word_gets_its_own_line():
    assert wrap_to_width("abcdefgh ij", 4, _measure) == ["abcdefgh", "ij"]


def test_empty_text_is_no_lines():
    assert wrap_to_width("", 10, _measure) == []
    assert wrap_to_width("   ", 10, _measure) == []

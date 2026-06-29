"""Card text layout — pure helper tests (lean nexus image)."""
from app.modules.reel.cards import format_identity_tag, wrap_to_width


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


def test_identity_tag_full_name_and_role():
    assert format_identity_tag("Punar Singh", "EMM Engineer") == "Punar · EMM Engineer"


def test_identity_tag_uses_only_first_name():
    assert format_identity_tag("Asha Rao Kumar", "Backend Engineer") == "Asha · Backend Engineer"


def test_identity_tag_name_only_when_role_missing():
    assert format_identity_tag("Punar Singh", None) == "Punar"
    assert format_identity_tag("Punar Singh", "  ") == "Punar"


def test_identity_tag_role_only_when_name_missing():
    assert format_identity_tag(None, "EMM Engineer") == "EMM Engineer"
    assert format_identity_tag("   ", "EMM Engineer") == "EMM Engineer"


def test_identity_tag_none_when_both_missing():
    assert format_identity_tag(None, None) is None
    assert format_identity_tag("  ", "") is None


def test_parse_point_glyph_defaults_to_star():
    from app.modules.reel.cards import parse_point_glyph, _ACCENT_SOFT
    glyph, phrase, rgb = parse_point_glyph("Strong on reliability")
    assert glyph == "★" and phrase == "Strong on reliability" and rgb == _ACCENT_SOFT


def test_parse_point_glyph_met_check():
    from app.modules.reel.cards import parse_point_glyph, _ACCENT_SOFT
    glyph, phrase, rgb = parse_point_glyph("✓ Met the bar on incident response")
    assert glyph == "✓" and phrase == "Met the bar on incident response"
    assert rgb == _ACCENT_SOFT


def test_parse_point_glyph_gap_is_neutral_not_accent():
    from app.modules.reel.cards import parse_point_glyph, _INK_SOFT, _ACCENT_SOFT
    glyph, phrase, rgb = parse_point_glyph("△ Scaling depth stayed shallow")
    assert glyph == "△" and phrase == "Scaling depth stayed shallow"
    assert rgb == _INK_SOFT and rgb != _ACCENT_SOFT

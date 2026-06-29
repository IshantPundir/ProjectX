"""The v4 director prompt loads and is verdict-aware."""
from app.ai.config import ai_config
from app.ai.prompts import PromptLoader


def test_director_prompt_version_is_v4():
    assert ai_config.reel_director_prompt_version == "v4"


def test_v4_director_prompt_is_verdict_aware():
    text = PromptLoader(version="v4").get("reel/director")
    assert text.strip()
    low = text.lower()
    # Branches on all three verdicts
    for verdict in ("advance", "borderline", "reject"):
        assert verdict in low
    # Mirrored anti-fabrication + neutral narration for non-advance
    assert "fabricat" in low or "defensible" in low
    assert "charity" in low
    # Polarity glyphs documented
    assert "★" in text and "✓" in text and "△" in text

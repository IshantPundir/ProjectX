"""Phase C fallback content tests — spec §5.5.

The FORBIDDEN_PHRASES list lives INLINE in this test file only.
Recreating it as a production constants module reintroduces exactly
the safety.py we deleted (spec §0). PR review enforces this discipline.
"""
from __future__ import annotations

import pytest

# FORBIDDEN by hand-review checklist. Inline in this test file only.
# Do NOT export, import, or re-create as a production constant module.
FORBIDDEN_PHRASES = (
    "passed",
    "failed",
    "rejected",
    "advanced",
    "unfortunately",
    "best of luck",
    "thanks for your interest",
)


def test_intro_fallback_uses_duration():
    """The intro fallback MUST parameterize target_duration_minutes — never
    hardcoded. A 30-minute senior interview falling back to "about 15
    minutes" erodes trust right after an infrastructure failure (spec §4.1
    Bug 2 fix)."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    text_30 = build_fallback_text(template_name="intro", target_duration_minutes=30)
    assert "30" in text_30
    assert "15" not in text_30

    text_45 = build_fallback_text(template_name="intro", target_duration_minutes=45)
    assert "45" in text_45


@pytest.mark.parametrize("duration", [5, 15, 30, 60])
def test_intro_fallback_length_le_50_words(duration):
    """Length cap (lenient on live, hard cap on hand-reviewed fallback)."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    text = build_fallback_text(template_name="intro", target_duration_minutes=duration)
    assert len(text.split()) <= 50


def test_wrap_normal_fallback_length_le_30_words():
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    text = build_fallback_text(template_name="wrap_normal")
    assert len(text.split()) <= 30


def test_ask_question_standard_fallback_is_verbatim():
    """The QuestionConfig.text is recruiter-validated content; the fallback
    asks it verbatim with no transition wrapper."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    q = "Walk me through how you'd handle a flaky integration test."
    assert build_fallback_text(template_name="ask_question_standard", question_text=q) == q


def test_fallback_strings_outcome_neutral():
    """Each fallback builder's output passes the inline FORBIDDEN_PHRASES
    check (case-insensitive substring)."""
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    intro = build_fallback_text(template_name="intro", target_duration_minutes=15)
    wrap = build_fallback_text(template_name="wrap_normal")
    asq = build_fallback_text(
        template_name="ask_question_standard",
        question_text="Tell me about your last project.",
    )

    for output in (intro, wrap, asq):
        lower = output.lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in lower, f"forbidden {phrase!r} in {output!r}"


def test_fallback_strings_no_salary_or_scheduling():
    """Inline checks for currency markers + scheduling commitments +
    hiring-manager mentions."""
    import re

    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    forbidden_substrings = [
        "$",
        "€",
        "£",
        "USD",
        "GBP",
        "salary",
        "i'll schedule",
        "we'll schedule",
        "hiring manager",
    ]
    for tn, kwargs in [
        ("intro", {"target_duration_minutes": 15}),
        ("wrap_normal", {}),
    ]:
        text = build_fallback_text(template_name=tn, **kwargs).lower()
        for forb in forbidden_substrings:
            assert forb.lower() not in text, f"forbidden {forb!r} in {tn} fallback: {text!r}"
        # No bare numbers that could be salary
        assert not re.search(r"\b\d{2,3}[,.]?\d{3}\b", text)


def test_build_fallback_text_unknown_template_raises():
    from app.modules.interview_engine.speech.fallbacks import build_fallback_text

    with pytest.raises(KeyError):
        build_fallback_text(template_name="nonexistent_template")

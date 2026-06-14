"""
Tests for app.modules.interview_engine.brain.policy

D5 task — deterministic policy gates (all run on the live critical path;
NONE may raise under any input).

Coverage:
  scrub_composed_say:
    - composed_say embeds rubric.excellent (≥12 chars) → returns fallback
    - clean conversational text → unchanged
    - None in → None out
    - short rubric string (<12 chars) coincidentally present → NOT a leak (no false-positive)
    - never raises on None/empty rubric strings

  coerce_probe_dimension:
    - valid unfired dimension slug → returned unchanged
    - fired proposal → coerced to first unfired
    - unknown slug → coerced to first unfired
    - all dimensions fired → returns None
    - cap reached even if unfired remain → returns None
    - empty follow_ups → returns None
    - never raises on garbage input
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.brain.policy import (
    SAFE_FALLBACK,
    coerce_probe_dimension,
    scrub_composed_say,
)
from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rubric(
    *,
    excellent: str = "Demonstrated clear ownership end-to-end with measurable impact",
    meets_bar: str = "Handled the core task with some guidance needed",
    below_bar: str = "Could not articulate the approach without heavy prompting",
    positive_evidence: list[str] | None = None,
    red_flags: list[str] | None = None,
    evaluation_hint: str = "Look for specificity and ownership language",
    follow_ups: list | None = None,
    fired_dimensions: list[str] | None = None,
) -> ActiveQuestionRubric:
    from app.modules.interview_engine.contracts import FollowUpDimension
    default_follow_ups = [
        FollowUpDimension(dimension="challenge", intent="depth", seed_probe="What was the biggest challenge?"),
        FollowUpDimension(dimension="measurement", intent="depth", seed_probe="How did you measure success?"),
    ]
    return ActiveQuestionRubric(
        question_id="q-1",
        text="Tell me about a project you led end to end.",
        excellent=excellent,
        meets_bar=meets_bar,
        below_bar=below_bar,
        positive_evidence=positive_evidence or ["mentions team coordination", "quantified outcome"],
        red_flags=red_flags or ["vague", "no personal ownership"],
        evaluation_hint=evaluation_hint,
        follow_ups=follow_ups if follow_ups is not None else default_follow_ups,
        fired_dimensions=fired_dimensions or [],
    )


# ===========================================================================
# scrub_composed_say
# ===========================================================================

class TestScrubComposedSay:
    def test_none_in_none_out(self):
        rubric = _make_rubric()
        assert scrub_composed_say(None, rubric) is None

    def test_clean_text_unchanged(self):
        rubric = _make_rubric()
        text = "Could you walk me through a specific example from your experience?"
        assert scrub_composed_say(text, rubric) == text

    def test_excellent_substring_is_a_leak(self):
        """composed_say embeds the rubric.excellent text → fallback returned."""
        rubric = _make_rubric(
            excellent="Demonstrated clear ownership end-to-end with measurable impact"
        )
        leaky = "That's great! You Demonstrated clear ownership end-to-end with measurable impact — thanks."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_meets_bar_substring_is_a_leak(self):
        rubric = _make_rubric(
            meets_bar="Handled the core task with some guidance needed"
        )
        leaky = f"It seems like you Handled the core task with some guidance needed."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_below_bar_substring_is_a_leak(self):
        rubric = _make_rubric(
            below_bar="Could not articulate the approach without heavy prompting"
        )
        leaky = "It sounds like you Could not articulate the approach without heavy prompting."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_positive_evidence_substring_is_a_leak(self):
        rubric = _make_rubric(positive_evidence=["mentions team coordination effort", "quantified outcome"])
        leaky = "I noticed you mentions team coordination effort in your answer."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_red_flag_substring_is_a_leak(self):
        rubric = _make_rubric(red_flags=["no personal ownership signal", "vague"])
        leaky = "Your answer shows no personal ownership signal which concerns me."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_evaluation_hint_substring_is_a_leak(self):
        rubric = _make_rubric(evaluation_hint="Look for specificity and ownership language")
        # The leaky text contains the evaluation_hint verbatim as a substring.
        leaky = "I want you to Look for specificity and ownership language in what you say next."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_case_insensitive_leak_detection(self):
        rubric = _make_rubric(
            excellent="Demonstrated clear ownership end-to-end with measurable impact"
        )
        leaky = "you DEMONSTRATED CLEAR OWNERSHIP END-TO-END WITH MEASURABLE IMPACT in that project."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_short_rubric_string_no_false_positive(self):
        """Rubric strings shorter than min_phrase_len (12) MUST NOT trigger a leak even if present."""
        rubric = _make_rubric(
            red_flags=["vague"],  # only 5 chars — well below min_phrase_len=12
        )
        text = "That's a bit vague — could you expand on that?"
        # "vague" is < 12 chars, must not be flagged
        result = scrub_composed_say(text, rubric)
        assert result == text

    def test_empty_rubric_strings_do_not_cause_false_positives(self):
        """Empty string secrets must not falsely match any text."""
        rubric = _make_rubric(
            excellent="",
            meets_bar="",
            below_bar="",
            evaluation_hint="",
            positive_evidence=[""],
            red_flags=[""],
        )
        text = "Thank you for sharing that with me."
        result = scrub_composed_say(text, rubric)
        assert result == text

    def test_none_rubric_strings_do_not_raise(self):
        """None text with any rubric → None, no exception."""
        rubric = _make_rubric()
        assert scrub_composed_say(None, rubric) is None

    def test_custom_fallback_is_returned(self):
        rubric = _make_rubric(
            excellent="Demonstrated clear ownership end-to-end with measurable impact"
        )
        leaky = "You Demonstrated clear ownership end-to-end with measurable impact."
        custom = "Let me rephrase that."
        result = scrub_composed_say(leaky, rubric, fallback=custom)
        assert result == custom

    def test_obvious_meta_phrase_is_a_leak(self):
        """Belt-and-suspenders: well-known meta-phrases → fallback even without rubric echo."""
        rubric = _make_rubric()
        leaky = "What I'm looking for is a candidate who can demonstrate ownership."
        result = scrub_composed_say(leaky, rubric)
        assert result == SAFE_FALLBACK

    def test_rubric_phrase_is_a_leak_even_if_short_when_other_triggers(self):
        """Verify that only the known-string path is checked for min_phrase_len — meta-phrases bypass it."""
        rubric = _make_rubric()
        text = "The rubric says you did great."
        result = scrub_composed_say(text, rubric)
        assert result == SAFE_FALLBACK


# ===========================================================================
# coerce_probe_dimension
# ===========================================================================

def _dims(*slugs):
    from app.modules.interview_engine.contracts import FollowUpDimension
    return [FollowUpDimension(dimension=s, intent="i", seed_probe="p") for s in slugs]


def test_coerce_returns_proposed_when_valid_and_unfired():
    assert coerce_probe_dimension("b", follow_ups=_dims("a", "b", "c"), fired=["a"], cap=2) == "b"


def test_coerce_fired_proposal_to_first_unfired():
    assert coerce_probe_dimension("a", follow_ups=_dims("a", "b", "c"), fired=["a"], cap=2) == "b"


def test_coerce_unknown_slug_to_first_unfired():
    assert coerce_probe_dimension("zzz", follow_ups=_dims("a", "b"), fired=[], cap=2) == "a"


def test_coerce_none_when_all_fired():
    assert coerce_probe_dimension("a", follow_ups=_dims("a", "b"), fired=["a", "b"], cap=5) is None


def test_coerce_none_when_cap_reached_even_if_unfired_remain():
    assert coerce_probe_dimension("c", follow_ups=_dims("a", "b", "c"), fired=["a", "b"], cap=2) is None


def test_coerce_none_when_no_follow_ups():
    assert coerce_probe_dimension("a", follow_ups=[], fired=[], cap=2) is None


def test_coerce_never_raises_on_garbage():
    assert coerce_probe_dimension(None, follow_ups=None, fired=None, cap=2) is None  # type: ignore[arg-type]

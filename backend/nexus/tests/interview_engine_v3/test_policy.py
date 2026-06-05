"""
Tests for app.modules.interview_engine.brain.policy

D5 task — deterministic policy gates (all run on the live critical path;
NONE may raise under any input).

Coverage:
  gate_knockout:
    - empty knockout_pending → pass-through (allow_move=True, forced_step=None, signal=None)
    - pending + unconfirmed + proposed close → block (allow_move=False, forced_step=current, signal set)
    - pending + unconfirmed + non-close move → allow but expose forced_step
    - after tracker.advance to confirmed + proposed close → pass-through
    - KnockoutTracker step progression: probe→check_alternatives→reflect_confirm→confirmed, idempotent at confirmed
    - never raises on weird inputs (empty pending, unseen signal)

  scrub_composed_say:
    - composed_say embeds rubric.excellent (≥12 chars) → returns fallback
    - clean conversational text → unchanged
    - None in → None out
    - short rubric string (<12 chars) coincidentally present → NOT a leak (no false-positive)
    - never raises on None/empty rubric strings

  coerce_probe_index:
    - valid unused index → returned unchanged
    - out-of-range index → coerced to first unused
    - already-used index → coerced to next unused
    - all probes used → returns None
    - empty follow_ups → returns None
    - None probe_index → coerced to first unused
    - never raises
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.brain.policy import (
    SAFE_FALLBACK,
    KnockoutGate,
    KnockoutStep,
    KnockoutTracker,
    coerce_probe_index,
    gate_knockout,
    scrub_composed_say,
)
from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric,
    BrainMove,
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
    follow_ups: list[str] | None = None,
    probes_used: list[int] | None = None,
) -> ActiveQuestionRubric:
    return ActiveQuestionRubric(
        question_id="q-1",
        text="Tell me about a project you led end to end.",
        excellent=excellent,
        meets_bar=meets_bar,
        below_bar=below_bar,
        positive_evidence=positive_evidence or ["mentions team coordination", "quantified outcome"],
        red_flags=red_flags or ["vague", "no personal ownership"],
        evaluation_hint=evaluation_hint,
        follow_ups=follow_ups or ["What was the biggest challenge?", "How did you measure success?"],
        probes_used=probes_used or [],
    )


# ===========================================================================
# KnockoutTracker — step progression
# ===========================================================================

class TestKnockoutTracker:
    def test_initial_step_is_probe_for_unseen_signal(self):
        tracker = KnockoutTracker()
        assert tracker.current_step("python_experience") == KnockoutStep.probe

    def test_advance_probe_to_check_alternatives(self):
        tracker = KnockoutTracker()
        tracker.advance("python_experience")
        assert tracker.current_step("python_experience") == KnockoutStep.check_alternatives

    def test_advance_check_alternatives_to_reflect_confirm(self):
        tracker = KnockoutTracker()
        tracker.advance("python_experience")
        tracker.advance("python_experience")
        assert tracker.current_step("python_experience") == KnockoutStep.reflect_confirm

    def test_advance_reflect_confirm_to_confirmed(self):
        tracker = KnockoutTracker()
        tracker.advance("python_experience")
        tracker.advance("python_experience")
        tracker.advance("python_experience")
        assert tracker.current_step("python_experience") == KnockoutStep.confirmed
        assert tracker.is_confirmed("python_experience") is True

    def test_advance_idempotent_at_confirmed(self):
        tracker = KnockoutTracker()
        for _ in range(6):
            tracker.advance("python_experience")
        assert tracker.current_step("python_experience") == KnockoutStep.confirmed
        assert tracker.is_confirmed("python_experience") is True

    def test_is_confirmed_false_for_unseen(self):
        tracker = KnockoutTracker()
        assert tracker.is_confirmed("unknown_signal") is False

    def test_independent_signals_do_not_interfere(self):
        tracker = KnockoutTracker()
        tracker.advance("signal_a")
        tracker.advance("signal_b")
        tracker.advance("signal_b")
        assert tracker.current_step("signal_a") == KnockoutStep.check_alternatives
        assert tracker.current_step("signal_b") == KnockoutStep.reflect_confirm

    def test_never_raises_on_advance_of_empty_string(self):
        tracker = KnockoutTracker()
        tracker.advance("")  # must not raise
        assert tracker.current_step("") == KnockoutStep.check_alternatives


# ===========================================================================
# gate_knockout
# ===========================================================================

class TestGateKnockout:
    # --- pass-through cases ---

    def test_empty_pending_always_passes(self):
        tracker = KnockoutTracker()
        result = gate_knockout(
            proposed_move=BrainMove.close,
            knockout_pending=[],
            tracker=tracker,
        )
        assert result.allow_move is True
        assert result.forced_step is None
        assert result.signal is None

    def test_empty_pending_non_close_passes(self):
        tracker = KnockoutTracker()
        result = gate_knockout(
            proposed_move=BrainMove.probe,
            knockout_pending=[],
            tracker=tracker,
        )
        assert result.allow_move is True
        assert result.forced_step is None

    def test_all_pending_confirmed_passes_close(self):
        tracker = KnockoutTracker()
        # advance signal to confirmed
        for _ in range(3):
            tracker.advance("python_experience")
        result = gate_knockout(
            proposed_move=BrainMove.close,
            knockout_pending=["python_experience"],
            tracker=tracker,
        )
        assert result.allow_move is True
        assert result.forced_step is None
        assert result.signal is None

    # --- blocking cases ---

    def test_pending_unconfirmed_close_is_blocked(self):
        tracker = KnockoutTracker()
        result = gate_knockout(
            proposed_move=BrainMove.close,
            knockout_pending=["python_experience"],
            tracker=tracker,
        )
        assert result.allow_move is False
        assert result.forced_step == KnockoutStep.probe  # first step for unseen signal
        assert result.signal == "python_experience"

    def test_blocked_close_uses_current_tracker_step(self):
        tracker = KnockoutTracker()
        tracker.advance("python_experience")  # now at check_alternatives
        result = gate_knockout(
            proposed_move=BrainMove.close,
            knockout_pending=["python_experience"],
            tracker=tracker,
        )
        assert result.allow_move is False
        assert result.forced_step == KnockoutStep.check_alternatives
        assert result.signal == "python_experience"

    def test_first_unconfirmed_signal_drives_the_gate(self):
        """When multiple signals are pending, the gate drives the first unconfirmed one."""
        tracker = KnockoutTracker()
        # confirm the first signal
        for _ in range(3):
            tracker.advance("signal_a")
        result = gate_knockout(
            proposed_move=BrainMove.close,
            knockout_pending=["signal_a", "signal_b"],
            tracker=tracker,
        )
        # signal_a is confirmed; signal_b is not → gate blocks on signal_b
        assert result.allow_move is False
        assert result.signal == "signal_b"
        assert result.forced_step == KnockoutStep.probe

    # --- allow + expose forced_step ---

    def test_non_close_with_pending_exposes_forced_step_but_allows(self):
        """Brain tries a probe; gate allows it but surfaces the pending knockout step."""
        tracker = KnockoutTracker()
        result = gate_knockout(
            proposed_move=BrainMove.probe,
            knockout_pending=["python_experience"],
            tracker=tracker,
        )
        assert result.allow_move is True
        assert result.forced_step == KnockoutStep.probe
        assert result.signal == "python_experience"

    def test_non_close_ask_with_pending_exposes_step(self):
        tracker = KnockoutTracker()
        tracker.advance("python_experience")  # at check_alternatives
        result = gate_knockout(
            proposed_move=BrainMove.ask,
            knockout_pending=["python_experience"],
            tracker=tracker,
        )
        assert result.allow_move is True
        assert result.forced_step == KnockoutStep.check_alternatives

    # --- never raises ---

    def test_never_raises_with_empty_pending_list(self):
        gate_knockout(proposed_move=BrainMove.close, knockout_pending=[], tracker=KnockoutTracker())

    def test_never_raises_with_weird_signal_name(self):
        gate_knockout(
            proposed_move=BrainMove.close,
            knockout_pending=[""],
            tracker=KnockoutTracker(),
        )

    def test_gate_knockout_returns_frozen_dataclass(self):
        result = gate_knockout(
            proposed_move=BrainMove.probe,
            knockout_pending=[],
            tracker=KnockoutTracker(),
        )
        assert isinstance(result, KnockoutGate)


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
# coerce_probe_index
# ===========================================================================

class TestCoerceProbeIndex:
    def test_valid_unused_index_returned_unchanged(self):
        result = coerce_probe_index(1, follow_ups=["A", "B", "C"], probes_used=[0])
        assert result == 1

    def test_first_index_valid_and_unused(self):
        result = coerce_probe_index(0, follow_ups=["A", "B"], probes_used=[])
        assert result == 0

    def test_out_of_range_coerced_to_first_unused(self):
        result = coerce_probe_index(99, follow_ups=["A", "B", "C"], probes_used=[0])
        assert result == 1  # 0 is used, so first available is 1

    def test_already_used_index_coerced_to_next_unused(self):
        result = coerce_probe_index(0, follow_ups=["A", "B", "C"], probes_used=[0])
        assert result == 1

    def test_negative_index_coerced_to_first_unused(self):
        result = coerce_probe_index(-1, follow_ups=["A", "B"], probes_used=[])
        assert result == 0

    def test_all_used_returns_none(self):
        result = coerce_probe_index(0, follow_ups=["A", "B"], probes_used=[0, 1])
        assert result is None

    def test_empty_follow_ups_returns_none(self):
        result = coerce_probe_index(0, follow_ups=[], probes_used=[])
        assert result is None

    def test_none_probe_index_coerced_to_first_unused(self):
        result = coerce_probe_index(None, follow_ups=["A", "B", "C"], probes_used=[0, 1])
        assert result == 2

    def test_none_probe_index_all_used_returns_none(self):
        result = coerce_probe_index(None, follow_ups=["A"], probes_used=[0])
        assert result is None

    def test_none_probe_index_empty_follow_ups_returns_none(self):
        result = coerce_probe_index(None, follow_ups=[], probes_used=[])
        assert result is None

    def test_first_available_after_gap_in_probes_used(self):
        """probes_used=[0, 2] → available=[1, 3, 4]; first is 1."""
        result = coerce_probe_index(
            99,
            follow_ups=["A", "B", "C", "D", "E"],
            probes_used=[0, 2],
        )
        assert result == 1

    # --- never raises ---

    def test_never_raises_empty_everything(self):
        result = coerce_probe_index(None, follow_ups=[], probes_used=[])
        assert result is None

    def test_never_raises_large_probes_used(self):
        """probes_used may reference out-of-range indices gracefully."""
        result = coerce_probe_index(0, follow_ups=["A"], probes_used=[0, 99, 100])
        assert result is None

    def test_never_raises_none_with_all_used(self):
        coerce_probe_index(None, follow_ups=["A", "B"], probes_used=[0, 1])

"""Tests for Continuation Coalescing decision + integration.

Spec: docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.orchestrator import (
    _CoalesceDecision,
    _PriorTurnSnapshot,
    _should_coalesce,
)


def _snapshot(
    *,
    completed_monotonic: float = 1000.0,
    speaker_emitted_content: bool = False,
    instruction_kind: str = "push_back",
    sub_context: str = "missing_specifics",
) -> _PriorTurnSnapshot:
    return _PriorTurnSnapshot(
        turn_id="prior-1",
        completed_monotonic=completed_monotonic,
        candidate_text="First one, like, I would communicate with the client.",
        instruction_kind=instruction_kind,
        sub_context=sub_context,
        speaker_emitted_content=speaker_emitted_content,
    )


class TestShouldCoalesce:
    def test_no_prior_turn_returns_false(self):
        decision = _should_coalesce(
            prior=None,
            now_monotonic=1000.0,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "no_prior_turn"

    def test_disabled_returns_false_even_when_otherwise_eligible(self):
        decision = _should_coalesce(
            prior=_snapshot(),
            now_monotonic=1000.1,
            coalesce_enabled=False,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "disabled"

    def test_prior_speaker_delivered_returns_false(self):
        decision = _should_coalesce(
            prior=_snapshot(speaker_emitted_content=True),
            now_monotonic=1000.1,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_gap_exceeds_window_returns_false(self):
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0),
            now_monotonic=1006.0,  # 6000ms after — beyond 5000ms window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    def test_gap_exactly_at_window_returns_false(self):
        """Boundary: gap_ms == coalesce_window_ms is NOT coalesced (strict <)."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0),
            now_monotonic=1005.0,  # exactly 5000ms after
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    @pytest.mark.parametrize("kind,sub_ctx", [
        ("redirect", "off_topic"),
        ("redirect", "abusive"),
        ("redirect", "injection"),
        ("polite_close", "default"),
        ("polite_close", "knockout"),
        ("repeat", "default"),
        ("deliver_first_question", "default"),
    ])
    def test_non_coalescible_kinds_return_false(self, kind, sub_ctx):
        decision = _should_coalesce(
            prior=_snapshot(instruction_kind=kind, sub_context=sub_ctx),
            now_monotonic=1000.5,  # 500ms — well within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "kind_not_coalescible"

    @pytest.mark.parametrize("kind,sub_ctx", [
        ("push_back", "vague_answer"),
        ("push_back", "deflection"),
        ("push_back", "missing_specifics"),
        ("push_back", "unanswered_subquestion"),
        ("clarify", "default"),
        ("deliver_question", "default"),
        ("deliver_question", "post_cap_advance"),
        ("deliver_probe", "default"),
        ("acknowledge_no_experience", "default"),
        ("redirect", "social_or_greeting"),
    ])
    def test_coalescible_kinds_with_undelivered_speaker_return_true(self, kind, sub_ctx):
        decision = _should_coalesce(
            prior=_snapshot(
                instruction_kind=kind,
                sub_context=sub_ctx,
                speaker_emitted_content=False,
            ),
            now_monotonic=1000.5,  # 500ms — well within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is True
        assert decision.reason == "coalesced"


class TestCapturePriorTurnSnapshot:
    """Verify _last_turn is populated correctly at every turn-completion path."""

    def test_capture_records_speaker_delivered_when_text_present_and_not_interrupted(
        self,
    ):
        from unittest.mock import MagicMock

        # Construct a bare orchestrator instance (no need for full DI) — we
        # only exercise _capture_prior_turn_snapshot which is a pure method
        # on self._last_turn.
        orch = MagicMock()
        orch._last_turn = None

        # Import the method as an unbound callable and bind to our mock.
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator,
        )
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-1",
            completed_monotonic=42.0,
            candidate_text="hello",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            final_text="What specifically did you set up first?",
            interrupted=False,
        )
        snap = orch._last_turn
        assert snap is not None
        assert snap.turn_id == "t-1"
        assert snap.candidate_text == "hello"
        assert snap.instruction_kind == "push_back"
        assert snap.sub_context == "missing_specifics"
        assert snap.speaker_emitted_content is True

    def test_capture_records_interrupted_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-2",
            completed_monotonic=43.0,
            candidate_text="basic slips",
            instruction_kind="push_back",
            sub_context="vague_answer",
            final_text="What specifically",  # partial text but interrupted
            interrupted=True,
        )
        assert orch._last_turn.speaker_emitted_content is False

    def test_capture_records_empty_speaker_output_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-3",
            completed_monotonic=44.0,
            candidate_text="something",
            instruction_kind="clarify",
            sub_context="default",
            final_text="",
            interrupted=False,
        )
        assert orch._last_turn.speaker_emitted_content is False

    def test_capture_records_whitespace_only_speaker_output_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-4",
            completed_monotonic=45.0,
            candidate_text="x",
            instruction_kind="clarify",
            sub_context="default",
            final_text="   \n  ",
            interrupted=False,
        )
        assert orch._last_turn.speaker_emitted_content is False

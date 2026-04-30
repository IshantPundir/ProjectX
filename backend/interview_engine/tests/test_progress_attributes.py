"""InterviewerAgent.publish_progress_attributes coverage.

The candidate's frontend ProgressBanner reads three string-valued
LiveKit participant attributes from the agent: current_question_index,
total_questions, and time_remaining_seconds. Without those attributes
the banner returns null and never renders, so the agent must publish
them on enter and after each turn.

These tests exercise the publish helper in isolation by patching the
``InterviewerAgent.session`` property. The full Agent lifecycle is not
needed -- the helper's only side effect is awaiting
``self.session.room.local_participant.set_attributes``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from agents.interviewer import InterviewerAgent
from app.modules.interview_runtime.schemas import (
    QuestionConfig,
    SessionConfig,
)
from config import InterviewEngineConfig
from state_machine import Action, SteeringObservation


def _make_question(idx: int) -> QuestionConfig:
    return QuestionConfig(
        id=f"00000000-0000-0000-0000-{idx:012d}",
        position=idx,
        text=f"Question {idx} placeholder text for testing.",
        signal_values=[f"signal_{idx}"],
        estimated_minutes=2.0,
        is_mandatory=False,
        follow_ups=[],
        positive_evidence=[
            "concrete trade-off named",
            "evidence of ownership",
            "specific scope numbers",
        ],
        red_flags=[
            "vague generalities",
            "outsources accountability",
        ],
        rubric={
            "excellent": "owned the failure mode and fixed it.",
            "meets_bar": "named the trade-off correctly.",
            "below_bar": "could not articulate any trade-offs.",
        },
        evaluation_hint="Probe once if the answer stays at the surface level.",
    )


def _make_session_config(*, n_questions: int, duration_minutes: int) -> SessionConfig:
    return SessionConfig.model_validate(
        {
            "session_id": "00000000-0000-0000-0000-000000000001",
            "job_title": "Senior Backend Engineer",
            "role_summary": "Owns the platform.",
            "seniority_level": "senior",
            "company": {
                "about": "We build infrastructure for mid-market AI startups today.",
                "industry": "ai_machine_learning",
                "company_stage": "series_a_b",
                "hiring_bar": "Engineers who own problems end-to-end with autonomy.",
            },
            "candidate": {"name": "Alex"},
            "stage": {
                "stage_id": "00000000-0000-0000-0000-000000000099",
                "stage_type": "ai_screening",
                "name": "Phone screen",
                "duration_minutes": duration_minutes,
                "difficulty": "medium",
                "questions": [
                    _make_question(i).model_dump() for i in range(n_questions)
                ],
                "advance_behavior": "manual_review",
            },
            "signals": [],
        }
    )


def _make_engine_config() -> InterviewEngineConfig:
    return InterviewEngineConfig(
        max_probes_per_question=2,
        time_warning_threshold=120,
        results_fallback_dir=Path("/tmp/engine-results-test"),
    )


def _build_agent(*, n_questions: int = 9, duration_minutes: int = 15) -> InterviewerAgent:
    return InterviewerAgent(
        session_config=_make_session_config(
            n_questions=n_questions, duration_minutes=duration_minutes
        ),
        engine_config=_make_engine_config(),
        nexus_jwt="fake-jwt",
        nexus_base_url="http://nexus:8000",
    )


@pytest.mark.asyncio
async def test_publish_progress_attributes_emits_initial_state():
    """On enter (state.start() called, no observations yet), the helper
    publishes index=0, total=N, and a positive time_remaining."""
    agent = _build_agent(n_questions=9, duration_minutes=15)
    agent.state_machine.state.start()

    set_attrs = AsyncMock()
    fake_session = MagicMock()
    fake_session.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        await agent._publish_progress_attributes()

    set_attrs.assert_awaited_once()
    payload = set_attrs.await_args.args[0]
    assert payload["current_question_index"] == "0"
    assert payload["total_questions"] == "9"
    # 15 minutes = 900s; allow a small fudge for elapsed time during the test.
    remaining = int(payload["time_remaining_seconds"])
    assert 800 <= remaining <= 900


@pytest.mark.asyncio
async def test_publish_progress_attributes_advances_index_after_turn():
    """After a turn that advances the state machine to question 2,
    current_question_index reflects the new index."""
    agent = _build_agent(n_questions=4, duration_minutes=15)
    agent.state_machine.state.start()

    # Drive the state machine forward: an observation that doesn't probe and
    # isn't disengagement → ASK_NEXT_QUESTION.
    obs = SteeringObservation(
        answer_summary="Walked through the trade-offs clearly.",
        signals_demonstrated=["signal_0"],
        wants_to_probe=False,
        candidate_disengaged=False,
        notes="solid answer",
    )
    action = agent.state_machine.decide_next_action(obs)
    agent.state_machine.execute_action(action)
    assert action == Action.ADVANCE
    assert agent.state_machine.state.current_question_index == 1

    set_attrs = AsyncMock()
    fake_session = MagicMock()
    fake_session.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        await agent._publish_progress_attributes()

    payload = set_attrs.await_args.args[0]
    assert payload["current_question_index"] == "1"
    assert payload["total_questions"] == "4"


@pytest.mark.asyncio
async def test_publish_progress_attributes_clamps_negative_remaining_to_zero():
    """If the interview has run past its duration, time_remaining must clamp
    to 0 (never negative — the frontend's bounds check rejects negatives,
    so a negative value would hide the banner instead of showing 0 min)."""
    agent = _build_agent(n_questions=2, duration_minutes=15)
    agent.state_machine.state.start()
    # Force elapsed_seconds() to exceed duration_limit_seconds by manipulating
    # the started_at monotonic clock anchor backwards.
    import time as _time

    agent.state_machine.state.started_at = _time.monotonic() - (
        agent.state_machine.state.duration_limit_seconds + 60
    )

    set_attrs = AsyncMock()
    fake_session = MagicMock()
    fake_session.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        await agent._publish_progress_attributes()

    payload = set_attrs.await_args.args[0]
    assert payload["time_remaining_seconds"] == "0"


@pytest.mark.asyncio
async def test_publish_progress_attributes_swallows_publish_failure():
    """A failure inside set_attributes (e.g. room not joined yet) must NOT
    propagate -- the state machine progression is the load-bearing thing."""
    agent = _build_agent(n_questions=2, duration_minutes=10)
    agent.state_machine.state.start()

    set_attrs = AsyncMock(side_effect=RuntimeError("room not connected"))
    fake_session = MagicMock()
    fake_session.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        # Must not raise.
        await agent._publish_progress_attributes()

    set_attrs.assert_awaited_once()

"""InterviewerAgent.publish_session_outcome coverage.

When the state machine reaches Action.CLOSE (or an engine-side error path
chooses to publish 'error'), the agent writes a session_outcome attribute
on its participant before shutdown. The candidate's frontend reads this
on the Disconnected event to route between CompletionScreen ('completed')
and DisconnectError with code ENGINE_ERROR ('error').

These tests exercise the publish helper in isolation by patching the
``InterviewerAgent.session`` property — same harness as
test_progress_attributes.py.
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


def _make_session_config() -> SessionConfig:
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
                "duration_minutes": 15,
                "difficulty": "medium",
                "questions": [_make_question(0).model_dump()],
                "advance_behavior": "manual_review",
            },
            "signals": [],
        }
    )


def _build_agent() -> InterviewerAgent:
    return InterviewerAgent(
        session_config=_make_session_config(),
        engine_config=InterviewEngineConfig(
            max_probes_per_question=2,
            time_warning_threshold=120,
            results_fallback_dir=Path("/tmp/engine-results-test"),
        ),
        nexus_jwt="fake-jwt",
        nexus_base_url="http://nexus:8000",
    )


@pytest.mark.asyncio
async def test_publish_session_outcome_completed():
    """On Action.CLOSE the engine writes session_outcome='completed' before shutdown."""
    agent = _build_agent()

    set_attrs = AsyncMock()
    fake_session = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        await agent._publish_session_outcome("completed")

    set_attrs.assert_awaited_once()
    payload = set_attrs.await_args.args[0]
    assert payload == {"session_outcome": "completed"}


@pytest.mark.asyncio
async def test_publish_session_outcome_error():
    """An engine-side error path can publish 'error' so the frontend renders
    DisconnectError with code ENGINE_ERROR rather than CompletionScreen."""
    agent = _build_agent()

    set_attrs = AsyncMock()
    fake_session = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        await agent._publish_session_outcome("error")

    payload = set_attrs.await_args.args[0]
    assert payload == {"session_outcome": "error"}


@pytest.mark.asyncio
async def test_publish_session_outcome_swallows_failure():
    """A failure inside set_attributes (e.g. room already disconnected) must
    NOT propagate — shutdown must continue regardless. The frontend will fall
    back to UNEXPECTED_DISCONNECT in that case, which is still better than
    crashing the agent on the way out."""
    agent = _build_agent()

    set_attrs = AsyncMock(side_effect=RuntimeError("room not connected"))
    fake_session = MagicMock()
    fake_session.room_io.room.local_participant.set_attributes = set_attrs

    with patch.object(
        InterviewerAgent, "session", new_callable=PropertyMock, return_value=fake_session
    ):
        # Must not raise.
        await agent._publish_session_outcome("completed")

    set_attrs.assert_awaited_once()

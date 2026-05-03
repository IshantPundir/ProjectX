"""Unit tests for signal-disclaim subsumption logic in InterviewController.

These tests construct an InterviewController WITHOUT a live AgentSession.
The signal-disclaim check is pure logic against controller state, so we
can assert it directly via _is_signal_disclaim_subsumed.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_runtime.schemas import (
    QuestionConfig,
    QuestionRubric,
)
from app.modules.tenant_settings import TenantSettings


def make_question(
    *,
    qid: str,
    signals: list[str],
) -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=0,
        text="A long enough placeholder question text body goes here.",
        signal_values=signals,
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["evidence_a", "evidence_b", "evidence_c"],
        red_flags=["red_flag_a", "red_flag_b"],
        rubric=QuestionRubric(
            excellent="excellent",
            meets_bar="meets-bar",
            below_bar="below-bar",
        ),
        evaluation_hint="evaluation hint at least 10 chars",
    )


def make_controller(session_config) -> InterviewController:
    """Build a controller with mocks for the LiveKit-dependent pieces.

    We need this to avoid a real AgentSession in unit tests. The
    controller's __init__ doesn't touch the session — it stores it
    on self only. The signal-disclaim check is a pure method.
    """
    return InterviewController(
        session_config=session_config,
        tenant_id=MagicMock(),
        correlation_id="test-corr",
        collector=MagicMock(),
        idle_nudge_config=IdleNudgeConfig(
            first_nudge_seconds=30.0,
            second_nudge_seconds=30.0,
            give_up_seconds=30.0,
        ),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
        ),
        tenant_settings=TenantSettings(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )


@pytest.fixture
def session_config():
    """Minimal valid SessionConfig with one question."""
    from tests.interview_engine.fixtures.mock_session_config import (
        load_live_data_session_config,
    )
    return load_live_data_session_config()


class TestHandleTaskResultUnionsSignals:
    def test_first_task_result_seeds_disqualified_signals(self, session_config):
        ctrl = make_controller(session_config)
        q = make_question(qid="q-a", signals=["python"])
        result = TaskResult(
            question_id=q.id,
            kind="technical_depth",
            signals_lacked=["python"],
        )
        ctrl._handle_task_result(q, result)
        assert "python" in ctrl._disqualified_signals

    def test_second_task_unions_into_disqualified(self, session_config):
        ctrl = make_controller(session_config)
        q1 = make_question(qid="q-a", signals=["python"])
        q2 = make_question(qid="q-b", signals=["sql"])
        ctrl._handle_task_result(
            q1,
            TaskResult(question_id=q1.id, kind="technical_depth", signals_lacked=["python"]),
        )
        ctrl._handle_task_result(
            q2,
            TaskResult(question_id=q2.id, kind="technical_depth", signals_lacked=["sql"]),
        )
        assert ctrl._disqualified_signals == {"python", "sql"}


class TestIsSignalDisclaimSubsumed:
    def test_false_when_no_disclaims(self, session_config):
        ctrl = make_controller(session_config)
        q = make_question(qid="q-x", signals=["python"])
        assert ctrl._is_signal_disclaim_subsumed(q) is False

    def test_true_when_all_signals_disclaimed(self, session_config):
        ctrl = make_controller(session_config)
        ctrl._disqualified_signals = {"python", "sql"}
        q = make_question(qid="q-x", signals=["python"])
        assert ctrl._is_signal_disclaim_subsumed(q) is True

    def test_false_with_partial_overlap(self, session_config):
        ctrl = make_controller(session_config)
        ctrl._disqualified_signals = {"python"}
        q = make_question(qid="q-x", signals=["python", "sql"])
        # SQL is not disclaimed, so the question can still surface signal.
        assert ctrl._is_signal_disclaim_subsumed(q) is False

    def test_true_when_question_signals_subset_of_disclaims(self, session_config):
        ctrl = make_controller(session_config)
        ctrl._disqualified_signals = {"python", "sql", "rust"}
        q = make_question(qid="q-x", signals=["python", "sql"])
        # Both q signals are disclaimed.
        assert ctrl._is_signal_disclaim_subsumed(q) is True

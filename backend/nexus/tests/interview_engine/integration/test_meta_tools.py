"""Integration test: meta tools (flag_safety_concern + report_technical_issue).

Drives the controller's meta-tool surface directly. The controller exposes
both as ``@function_tool`` decorated methods on the Agent. The
``FunctionTool`` descriptor in livekit-agents binds the tool on access so
``await ctrl.flag_safety_concern(...)`` is invokable like an ordinary
async method.

Avoids LLM-driven flow because both tools' triggers ("you're being weird"
or "I can't hear you") are not deterministic LLM intents — calling the
tool method directly is the cleanest correctness assertion.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.tenant_settings import TenantSettings
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


pytestmark = pytest.mark.asyncio


def _make_controller():
    cfg = load_live_data_session_config()
    collector = EventCollector(
        session_id=cfg.session_id,
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="test-correlation",
        controller_prompt_hash="sha256:test",
        model_versions={},
        redaction_mode="metadata",
    )
    ctrl = InterviewController(
        session_config=cfg,
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id="test-correlation",
        collector=collector,
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
    return ctrl, collector


async def test_flag_safety_concern_records_audit_event_without_terminating():
    ctrl, collector = _make_controller()
    fake_ctx = MagicMock()

    result = await ctrl.flag_safety_concern(
        fake_ctx,
        category="harassment",
        note="Candidate made an inappropriate remark.",
    )

    assert "Concern recorded" in result
    events = collector.events_of_kind("controller.intent.flag_safety_concern")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["category"] == "harassment"
    assert payload["note_chars"] == len("Candidate made an inappropriate remark.")
    # Tool MUST NOT terminate the interview.
    assert ctrl._end_outcome is None
    assert ctrl._terminated is False


async def test_report_technical_issue_records_audit_event_without_terminating():
    ctrl, collector = _make_controller()
    fake_ctx = MagicMock()

    result = await ctrl.report_technical_issue(
        fake_ctx,
        description="Audio is choppy and dropping every few seconds.",
    )

    assert "Issue logged" in result
    events = collector.events_of_kind("controller.intent.report_technical_issue")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["description_chars"] == len(
        "Audio is choppy and dropping every few seconds."
    )
    # Tool MUST NOT terminate.
    assert ctrl._end_outcome is None
    assert ctrl._terminated is False


@pytest.mark.parametrize(
    "category",
    [
        "harassment",
        "threats_to_self",
        "threats_to_others",
        "inappropriate_request",
        "other",
    ],
)
async def test_flag_safety_concern_with_each_category(category: str):
    """Spot-check every category emits the same shape."""
    ctrl, collector = _make_controller()
    fake_ctx = MagicMock()
    await ctrl.flag_safety_concern(fake_ctx, category=category, note="note body.")
    events = collector.events_of_kind("controller.intent.flag_safety_concern")
    assert len(events) == 1
    assert events[0].payload["category"] == category
    assert ctrl._end_outcome is None


async def test_meta_tools_can_fire_repeatedly_in_one_session():
    """Both tools are append-only; multiple invocations should accumulate."""
    ctrl, collector = _make_controller()
    fake_ctx = MagicMock()

    await ctrl.flag_safety_concern(fake_ctx, category="harassment", note="first.")
    await ctrl.report_technical_issue(fake_ctx, description="audio drop 1.")
    await ctrl.flag_safety_concern(fake_ctx, category="other", note="second.")
    await ctrl.report_technical_issue(fake_ctx, description="audio drop 2.")

    safety = collector.events_of_kind("controller.intent.flag_safety_concern")
    technical = collector.events_of_kind("controller.intent.report_technical_issue")
    assert len(safety) == 2
    assert len(technical) == 2
    assert ctrl._end_outcome is None

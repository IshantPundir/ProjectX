"""Integration test: tenant_settings.engine_agent_name override flows
through to build_controller_prompt + controller.started log.

Phase 5 / Task 6 — verifies the controller plumbing for the per-tenant
agent_name override:

  * `build_controller_prompt(config, agent_name=...)` substitutes the
    explicit `agent_name` into the rendered prompt body (no env-fallback
    inside the function — the caller pre-resolves).
  * `InterviewController.__init__(tenant_settings=...)` derives both
    `_tenant_policy` and `_agent_name`. When `engine_agent_name` is set,
    `_agent_name_override_active` is True; when None, the env value
    (`settings.engine_agent_name`) substitutes and the flag is False.

Behavior preservation contract: default `TenantSettings(tenant_id=...)`
yields the same observable behavior as the prior hardcoded
`tenant_policy="record_only"` + env-only `agent_name`.
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import (
    InterviewController,
    build_controller_prompt,
)
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.tenant_settings import TenantSettings
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


def _build_collector() -> EventCollector:
    return EventCollector(
        session_id="00000000-7d96-c5d1-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="corr-1",
        controller_prompt_hash="sha256:abc",
        model_versions={},
        redaction_mode="metadata",
    )


def test_build_controller_prompt_with_override() -> None:
    config = load_live_data_session_config()
    rendered = build_controller_prompt(config, agent_name="Acme-Bot")
    assert "Acme-Bot" in rendered


def test_build_controller_prompt_default_uses_arg() -> None:
    """No env-fallback inside build_controller_prompt — the caller
    pre-resolves the env fallback. The function only substitutes."""
    config = load_live_data_session_config()
    rendered = build_controller_prompt(config, agent_name="Dakota-1785")
    assert "Dakota-1785" in rendered


def test_controller_init_with_explicit_agent_name() -> None:
    config = load_live_data_session_config()
    tenant_settings = TenantSettings(
        tenant_id=uuid.uuid4(),
        engine_knockout_policy="record_only",
        engine_agent_name="Acme-Bot",
    )
    ctrl = InterviewController(
        session_config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="corr-1",
        collector=_build_collector(),
        idle_nudge_config=IdleNudgeConfig(60.0, 60.0, 60.0),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=30.0,
        ),
        tenant_settings=tenant_settings,
    )
    assert ctrl._agent_name == "Acme-Bot"
    assert ctrl._tenant_policy == "record_only"
    assert ctrl._agent_name_override_active is True


def test_controller_init_falls_back_to_env_when_null() -> None:
    """engine_agent_name=None falls back to settings.engine_agent_name (the env value)."""
    from app.config import settings as app_settings

    config = load_live_data_session_config()
    tenant_settings = TenantSettings(
        tenant_id=uuid.uuid4(),
        engine_knockout_policy="record_only",
        engine_agent_name=None,
    )
    ctrl = InterviewController(
        session_config=config,
        tenant_id=uuid.uuid4(),
        correlation_id="corr-1",
        collector=_build_collector(),
        idle_nudge_config=IdleNudgeConfig(60.0, 60.0, 60.0),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=900.0,
            overhead_seconds=30.0,
        ),
        tenant_settings=tenant_settings,
    )
    assert ctrl._agent_name == app_settings.engine_agent_name
    assert ctrl._agent_name_override_active is False

"""Shared fixtures for prompt-quality tests.

All tests in this directory use the production LLM (real OpenAI calls).
Auto-applies the @pytest.mark.prompt_quality marker so per-PR CI skips them.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from livekit.agents import AgentSession, inference

from app.ai.config import ai_config
from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import IdleNudgeConfig
from app.modules.tenant_settings import TenantSettings
from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


def pytest_collection_modifyitems(config, items):
    """Auto-apply @pytest.mark.prompt_quality to every test in this dir."""
    for item in items:
        if "prompt_quality" in str(item.fspath):
            item.add_marker(pytest.mark.prompt_quality)


@pytest.fixture
def session_config():
    return load_live_data_session_config()


@pytest_asyncio.fixture
async def production_llm():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set; skipping prompt-quality tests")
    return inference.LLM(model=ai_config.interview_llm_model)


@pytest_asyncio.fixture
async def agent_session(session_config, production_llm):
    """AgentSession + InterviewController with production prompt + production LLM."""
    collector = EventCollector(
        session_id=session_config.session_id,
        tenant_id="00000000-0000-0000-0000-000000000001",
        correlation_id="prompt-quality-test",
        controller_prompt_hash="sha256:test",
        model_versions={},
        redaction_mode="metadata",
    )
    session = AgentSession(llm=production_llm)
    controller = InterviewController(
        session_config=session_config,
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id="prompt-quality-test",
        collector=collector,
        idle_nudge_config=IdleNudgeConfig(30.0, 30.0, 30.0),
        budget=SessionBudget(
            started_at_monotonic=0.0,
            duration_limit_seconds=session_config.stage.duration_minutes * 60.0,
        ),
        tenant_settings=TenantSettings(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        ),
    )
    await session.start(controller)
    yield session, controller, collector
    await session.aclose()

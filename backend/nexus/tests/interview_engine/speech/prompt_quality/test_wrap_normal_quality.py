"""Real-LLM tests for wrap_normal template. Nightly only."""
from __future__ import annotations

import re

import pytest

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.speech import SpeechAgent
from app.modules.interview_engine.speech import deliveries

OUTCOME_RE = re.compile(
    r"\b(best of luck|thanks for your interest|unfortunately|"
    r"passed|failed|rejected)\b", re.IGNORECASE,
)


@pytest.fixture
def collector():
    return EventCollector(
        session_id="test-session", tenant_id="test-tenant",
        correlation_id="test-corr", controller_prompt_hash="sha256:test",
        task_prompt_hashes={}, model_versions={}, redaction_mode="metadata",
    )


@pytest.fixture
def speech_agent(collector):
    return SpeechAgent(
        client=get_openai_raw_client(),
        model=ai_config.speech_agent_model,
        effort=ai_config.speech_agent_effort or None,
        collector=collector,
    )


@pytest.mark.prompt_quality
@pytest.mark.parametrize("invocation", [1, 2, 3])
@pytest.mark.asyncio
async def test_wrap_normal_real_llm_no_outcome_implications(
    speech_agent, invocation,
):
    handle = await deliveries.render_wrap_normal(speech_agent)
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    assert not OUTCOME_RE.search(full), f"Outcome leak (run {invocation}): {full!r}"

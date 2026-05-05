"""Real-LLM tests for ask_question_standard template. Nightly only."""
from __future__ import annotations

import re

import pytest

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.speech import SpeechAgent
from app.modules.interview_engine.speech import deliveries


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
@pytest.mark.parametrize("question,must_contain", [
    ("Walk me through how you'd handle a flaky integration test.",
     ["flaky", "integration test"]),
    ("Tell me about a time you had to debug a production issue.",
     ["debug", "production"]),
    ("What's your approach to writing maintainable code?",
     ["maintainable", "code"]),
])
@pytest.mark.asyncio
async def test_ask_question_standard_real_llm_preserves_meaning(
    speech_agent, question, must_contain,
):
    handle = await deliveries.render_ask_question_standard(
        speech_agent, question_text=question,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks).lower()
    for phrase in must_contain:
        assert phrase.lower() in full, f"Lost {phrase!r} in: {full!r}"

"""Real-LLM tests for intro template. Nightly only.

Run via:
    docker compose run --rm -e OPENAI_API_KEY=$OPENAI_API_KEY nexus \
        pytest tests/interview_engine/speech/prompt_quality/test_intro_quality.py -m prompt_quality -v
"""
from __future__ import annotations

import re

import pytest

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.speech import SpeechAgent
from app.modules.interview_engine.speech import deliveries

OUTCOME_WORDS_RE = re.compile(
    r"\b(passed|failed|rejected|advanced|unfortunately|best of luck|"
    r"thanks for your interest)\b",
    re.IGNORECASE,
)


@pytest.fixture
def collector():
    # Minimal real EventCollector — write to /dev/null sink
    return EventCollector(
        session_id="test-session",
        tenant_id="test-tenant",
        correlation_id="test-corr",
        controller_prompt_hash="sha256:test",
        task_prompt_hashes={},
        model_versions={},
        redaction_mode="metadata",
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
@pytest.mark.parametrize("first_name,role,minutes", [
    ("Alex", "Backend Engineer", 15),
    ("Priya", "Senior SRE", 30),
    ("Mahmoud", "Frontend Engineer", 20),
    ("Lin", "Data Scientist", 45),
    ("Sam", "Product Designer", 10),
])
@pytest.mark.asyncio
async def test_intro_real_llm_no_outcome_words(speech_agent, first_name, role, minutes):
    handle = await deliveries.render_intro(
        speech_agent,
        candidate_first_name=first_name,
        role_title=role,
        target_duration_minutes=minutes,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    assert not OUTCOME_WORDS_RE.search(full), f"Outcome word in: {full!r}"


@pytest.mark.prompt_quality
@pytest.mark.asyncio
async def test_intro_real_llm_length_target(speech_agent):
    handle = await deliveries.render_intro(
        speech_agent,
        candidate_first_name="Alex", role_title="Engineer", target_duration_minutes=15,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    # Lenient cap (per Q4 A2): 50 + 30% slack for prompt iteration headroom
    assert len(full.split()) <= 65, f"Length {len(full.split())} exceeds lenient cap"


@pytest.mark.prompt_quality
@pytest.mark.asyncio
async def test_intro_real_llm_does_not_mention_question_count(speech_agent):
    handle = await deliveries.render_intro(
        speech_agent,
        candidate_first_name="Alex", role_title="Engineer", target_duration_minutes=15,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)
    # No digit followed by "questions"
    assert not re.search(r"\b\d+\s*questions\b", full, re.IGNORECASE)

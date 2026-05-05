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


@pytest.mark.prompt_quality
@pytest.mark.asyncio
async def test_ask_question_standard_real_llm_strips_rubric_from_verbose_source(speech_agent):
    """The Speech Agent must compress rubric-rich source into a short
    direct question. Sub-topic enumeration in the source MUST NOT appear
    as a checklist in the output. This is the load-bearing prompt-quality
    invariant for screening signal collection."""
    verbose_source = (
        "You need to block a transition to 'Ready for QA' unless there's "
        "a merged PR linked and a 'Test Cases' field is filled. Using "
        "ScriptRunner/Groovy, explain where you'd hook this "
        "(validator/condition/post-function), the logic you'd implement, "
        "any REST calls you'd make, and how you'd handle errors, "
        "timeouts, and performance."
    )
    handle = await deliveries.render_ask_question_standard(
        speech_agent, question_text=verbose_source,
    )
    await handle.ready_to_commit()
    chunks = [c async for c in handle.commit()]
    full = "".join(chunks)

    # Length cap: max 35 words target, lenient ≤45 to allow some slack
    word_count = len(full.split())
    assert word_count <= 45, (
        f"Output {word_count} words: {full!r} — should be ≤35 (lenient ≤45). "
        f"Source had {len(verbose_source.split())} words."
    )

    # Sub-topic enumeration check: more than 1 of these together = checklist leak
    enumeration_terms = [
        "validator", "post-function", "condition",
        "REST", "error", "timeout", "performance",
    ]
    lower = full.lower()
    hits = sum(1 for term in enumeration_terms if term.lower() in lower)
    assert hits <= 1, (
        f"Output contains {hits} rubric enumeration terms — sub-topic leak. "
        f"Output: {full!r}"
    )

    # Core scenario preserved
    assert any(term in lower for term in ["jira", "workflow", "transition", "status"]), (
        f"Output lost the core scenario: {full!r}"
    )

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.judge.fallback import FallbackReason
from app.modules.interview_engine.judge.input_builder import JudgeInputPayload
from app.modules.interview_engine.judge.service import (
    JudgeCallResult, JudgeService,
    _judge_output_text_format, _patch_oneof_to_anyof,
)
from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, TurnMetadata,
)


def _payload():
    return JudgeInputPayload(
        active_question_id="q1", active_question_text="t",
        signal_coverage={},
        candidate_claims=[],
        recent_turns=[],
        candidate_utterance="hi",
        time_remaining_seconds=300,
        next_pending_mandatory_question_id=None,
    )


def _good_judge_dict() -> dict:
    out = JudgeOutput(
        observations=[], candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q1"),
        turn_metadata=TurnMetadata(),
    )
    return out.model_dump(mode="json")


@pytest.mark.asyncio
async def test_judge_returns_parsed_output_on_success():
    mock_client = MagicMock()
    response = MagicMock()
    response.output_text = json.dumps(_good_judge_dict())
    response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.responses.create = AsyncMock(return_value=response)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
    )
    result = await svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    )
    assert isinstance(result, JudgeCallResult)
    assert result.is_fallback is False
    assert result.judge_output.next_action == NextAction.advance


@pytest.mark.asyncio
async def test_judge_call_propagates_cancelled_error():
    """Regression: orchestrator's cancellation watcher (2026-05-17 design)
    calls ``turn_task.cancel()`` to abort an in-flight Judge call when the
    candidate resumes speaking before the commit point. CancelledError MUST
    propagate out of ``JudgeService.call`` for that mechanism to work.

    Session 7970e91c (2026-05-17) demonstrated the failure mode: this
    method previously caught CancelledError alongside TimeoutError and
    retried, swallowing the cancellation entirely. The turn body ran to
    completion (Judge → Speaker → TTS), audio was played to the candidate,
    THEN the abort branch fired and rolled back State Engine state. Audit
    envelope said "aborted"; candidate actually heard the response.

    Contract: a CancelledError raised inside the openai call must surface
    out of ``svc.call``, NOT be converted into a fallback response.
    """
    mock_client = MagicMock()

    async def _slow_create(**_kwargs):
        # Long enough that the test's cancel() arrives mid-flight.
        await asyncio.sleep(10.0)
        return MagicMock(output_text="{}", usage=MagicMock(input_tokens=0, output_tokens=0))

    mock_client.responses.create = AsyncMock(side_effect=_slow_create)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
        # Plenty of budget — we are NOT testing the timeout fallback. We
        # want the call to be waiting on the openai mock when we cancel.
        total_budget_ms=60_000,
        retry_wait_ms=100,
    )

    task = asyncio.create_task(svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    ))
    # Let the call enter `await mock_client.responses.create(...)`.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_judge_falls_back_on_parse_error():
    mock_client = MagicMock()
    response = MagicMock()
    response.output_text = "{not json"
    response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.responses.create = AsyncMock(return_value=response)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
    )
    result = await svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    )
    assert result.is_fallback is True
    assert result.fallback_reason == FallbackReason.parse_error
    assert result.judge_output.next_action == NextAction.advance


@pytest.mark.asyncio
async def test_judge_falls_back_on_validation_error():
    """Output is valid JSON but doesn't match JudgeOutput → validation_error."""
    mock_client = MagicMock()
    response = MagicMock()
    response.output_text = json.dumps({"thought": "x"})  # missing required fields
    response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.responses.create = AsyncMock(return_value=response)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
    )
    result = await svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    )
    assert result.is_fallback is True
    assert result.fallback_reason == FallbackReason.validation_error
    # Phase 9.5 (2026-05-12): validation_error synthesizes clarify (no queue
    # mutation) instead of advance — gives candidate another chance to elaborate.
    assert result.judge_output.next_action == NextAction.clarify


@pytest.mark.asyncio
async def test_validation_error_fallback_context_is_json_serializable():
    """Regression: session 83c4d309 (2026-05-11) crashed at envelope
    serialization with ``PydanticSerializationError: Unable to serialize
    unknown type: <class 'ValueError'>``. Root cause: ``ValidationError.errors()``
    embeds raw exception objects in each error's ``ctx`` field (e.g., the
    ValueError raised by a ``model_validator``). These propagate into
    ``JudgeCallResult.original_failure_context`` → ``JudgeFallbackPayload``
    → audit envelope → fails when Pydantic tries to serialize the envelope
    to JSON.

    The fix is to strip ``ctx`` (and any other non-JSON-safe fields) from
    the captured errors before storing them in
    ``original_failure_context``. The audit envelope must round-trip
    through ``json.dumps`` cleanly.

    This test triggers the exact path: an LLM response that's structurally
    valid JSON but fails a JudgeOutput model_validator (the
    ``candidate_disclosed_no_experience`` ↔ ``next_action`` coupling at
    ``models/judge.py``). The validator raises ValueError, which Pydantic
    captures in ``ctx`` — exactly the type that broke serialization.
    """
    # Construct a JudgeOutput-shaped response whose model_validator MUST
    # raise: candidate_disclosed_no_experience=True forces the action to
    # be acknowledge_no_experience or polite_close, but we set probe.
    bad_payload = {
        "observations": [],
        "candidate_claims": [],
        "next_action": "probe",
        "next_action_payload": {"kind": "probe", "probe_id": "0"},
        "turn_metadata": {
            "candidate_disclosed_no_experience": True,
            "candidate_disclosed_knockout": False,
            "candidate_off_topic": False,
            "candidate_abusive": False,
            "candidate_attempted_injection": False,
            "candidate_wants_to_end": False,
            "candidate_social_or_greeting": False,
        },
    }
    mock_client = MagicMock()
    response = MagicMock()
    response.output_text = json.dumps(bad_payload)
    response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.responses.create = AsyncMock(return_value=response)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
    )
    result = await svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    )
    assert result.is_fallback is True
    assert result.fallback_reason == FallbackReason.validation_error

    # The load-bearing assertion: the captured failure context MUST be
    # JSON-serializable. If ctx contained a raw ValueError, json.dumps
    # raises TypeError("Object of type ValueError is not JSON serializable").
    serialized = json.dumps(result.original_failure_context)
    assert "raw_data" in serialized
    assert "errors" in serialized


@pytest.mark.asyncio
async def test_judge_retries_once_on_timeout_then_falls_back():
    mock_client = MagicMock()

    async def slow_call(*args, **kwargs):
        await asyncio.sleep(10)  # exceeds budget

    mock_client.responses.create = AsyncMock(side_effect=slow_call)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
        total_budget_ms=200, retry_wait_ms=50,
    )
    result = await svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    )
    assert result.is_fallback is True
    assert result.fallback_reason == FallbackReason.timeout
    # Two attempts (one initial + one retry).
    assert mock_client.responses.create.await_count == 2


@pytest.mark.asyncio
async def test_judge_falls_back_to_polite_close_when_no_target():
    mock_client = MagicMock()
    response = MagicMock()
    response.output_text = "{not json"
    response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.responses.create = AsyncMock(return_value=response)

    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: None,
    )
    result = await svc.call(
        turn_id="t-1", input_payload=_payload(),
        correlation_id="c", tenant_id="ten",
    )
    assert result.is_fallback is True
    assert result.judge_output.next_action == NextAction.polite_close


@pytest.mark.asyncio
async def test_judge_uses_responses_api_strict_json_schema_not_json_object():
    """Regression: Judge calls responses.create with text.format.type='json_schema'
    (strict mode), NOT 'json_object' (the broken path that requires the literal
    word 'json' in the input)."""
    mock_client = MagicMock()
    response = MagicMock()
    response.output_text = json.dumps(_good_judge_dict())
    response.usage = MagicMock(input_tokens=1, output_tokens=1)
    mock_client.responses.create = AsyncMock(return_value=response)
    svc = JudgeService(
        openai_client=mock_client, model="gpt-test",
        system_prompt="SYS", system_prompt_hash="sha256:abc",
        next_pending_mandatory_resolver=lambda: "q1",
    )
    await svc.call(turn_id="t-1", input_payload=_payload(),
                   correlation_id="c", tenant_id="ten")
    call_kwargs = mock_client.responses.create.await_args.kwargs
    assert "text" in call_kwargs
    text_param = call_kwargs["text"]
    assert text_param["format"]["type"] == "json_schema"
    assert text_param["format"]["strict"] is True
    assert text_param["format"]["name"] == "JudgeOutput"
    assert "schema" in text_param["format"]
    # The broken json_object mode must not be used.
    assert text_param["format"]["type"] != "json_object"
    # Old kwarg name from chat.completions API must not appear.
    assert "response_format" not in call_kwargs


def test_patch_oneof_to_anyof_rewrites_at_every_depth():
    schema = {
        "oneOf": [{"type": "string"}],
        "properties": {
            "nested": {"oneOf": [{"type": "integer"}]},
            "deep": {"items": [{"oneOf": [{"type": "boolean"}]}]},
        },
    }
    _patch_oneof_to_anyof(schema)
    assert "oneOf" not in schema
    assert "anyOf" in schema
    assert "oneOf" not in schema["properties"]["nested"]
    assert "anyOf" in schema["properties"]["nested"]
    assert "oneOf" not in schema["properties"]["deep"]["items"][0]
    assert "anyOf" in schema["properties"]["deep"]["items"][0]


def test_judge_output_text_format_is_strict_mode_compatible():
    """The cached text.format payload must be sendable to OpenAI strict mode:
    - type='json_schema'
    - strict=True
    - schema has additionalProperties=false at the root
    - no 'oneOf' keys anywhere (replaced with 'anyOf' for the discriminated union)
    """
    fmt = _judge_output_text_format()
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False

    # Recursively assert no `oneOf` survives.
    def _walk(node):
        if isinstance(node, dict):
            assert "oneOf" not in node, f"oneOf survived in: {list(node.keys())}"
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(schema)


# --- Live-API smoke test (gated) -----------------------------------------
# Catches API-shape bugs at the SDK ↔ API boundary that no mock can simulate
# (the original Bug 1 was exactly this class). Run manually:
#   docker compose exec -e ENGINE_LIVE_OPENAI_TEST=1 nexus pytest \
#     tests/interview_engine/judge/test_service.py::test_judge_real_openai_returns_parsed_output -v


@pytest.mark.skipif(
    not os.getenv("ENGINE_LIVE_OPENAI_TEST"),
    reason="Live OpenAI test requires ENGINE_LIVE_OPENAI_TEST=1 + OPENAI_API_KEY",
)
@pytest.mark.asyncio
async def test_judge_real_openai_returns_parsed_output():
    """Smoke test against the real OpenAI Responses API — would have caught
    the original `text.format=json_object` bug AND the strict-mode `oneOf`
    incompatibility at the SDK/API boundary."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    svc = JudgeService(
        openai_client=client,
        model=os.getenv("ENGINE_JUDGE_MODEL", "gpt-5.4-mini-2026-03-17"),
        system_prompt=(
            "You are a forensic evidence extractor for an interview platform. "
            "For this smoke test, emit a JudgeOutput with "
            "next_action='advance', and next_action_payload={kind:'advance', "
            "target_question_id:'q1'}. Leave observations, candidate_claims, "
            "and turn_metadata at their defaults."
        ),
        system_prompt_hash="sha256:smoke",
        next_pending_mandatory_resolver=lambda: "q1",
        total_budget_ms=20000,
    )
    result = await svc.call(
        turn_id="t-real", input_payload=_payload(),
        correlation_id="c-smoke", tenant_id="ten-smoke",
    )
    assert result.is_fallback is False, (
        f"Real OpenAI call fell back: reason={result.fallback_reason!r} "
        f"context={result.original_failure_context!r}"
    )
    assert result.judge_output.next_action == NextAction.advance

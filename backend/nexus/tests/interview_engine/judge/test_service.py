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
    assert result.judge_output.next_action == NextAction.advance


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

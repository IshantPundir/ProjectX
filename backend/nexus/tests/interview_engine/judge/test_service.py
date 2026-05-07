import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.judge.fallback import FallbackReason
from app.modules.interview_engine.judge.input_builder import JudgeInputPayload
from app.modules.interview_engine.judge.service import (
    JudgeCallResult, JudgeService,
)
from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, TurnMetadata,
)
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot


def _payload():
    return JudgeInputPayload(
        active_question_id="q1", active_question_text="t",
        ledger_snapshot=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        queue_snapshot=QuestionQueueSnapshot(),
        claims_snapshot=ClaimsPoolSnapshot(),
        recent_turns=[], candidate_utterance="hi", time_remaining_seconds=300,
    )


def _good_judge_dict() -> dict:
    out = JudgeOutput(
        thought="ok", observations=[], candidate_claims=[],
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
async def test_judge_uses_responses_api_text_format_not_response_format():
    """Regression: Responses API uses text={"format":...}, not response_format=."""
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
    assert call_kwargs["text"] == {"format": {"type": "json_object"}}
    assert "response_format" not in call_kwargs

"""Tests for the communication judge.

Mocks at the app/ai boundary — no real LLM calls.
Patch target: ``app.modules.reporting.scoring.judge.get_raw_openai_client``

The fake response mirrors the real ``ParsedResponse`` accessor contract:
- ``response.output_parsed`` returns the parsed Pydantic model (fast path).
- ``response.usage`` has ``input_tokens_details.cached_tokens`` (new shape
  in the Responses API — differs from chat.completions' prompt_tokens_details).

The per-answer BARS judge (``grade_answer`` / ``grade_answer_consistent``) was
removed in the report-generator redesign; only ``grade_communication`` remains.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.reporting.schemas import CommunicationVerdict
from app.modules.reporting.scoring.judge import grade_communication


def _make_fake_response(verdict: object, cached: int) -> SimpleNamespace:
    """Build a fake ParsedResponse-shaped object."""
    return SimpleNamespace(
        output_parsed=verdict,
        output=[],  # not walked when output_parsed is set
        usage=SimpleNamespace(
            input_tokens=900,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
            output_tokens=40,
        ),
    )


def _make_refusal_response() -> SimpleNamespace:
    """Fake ParsedResponse where the model returned a refusal content item."""
    refusal_content = SimpleNamespace(type="refusal", refusal="Content policy violation")
    message_item = SimpleNamespace(type="message", content=[refusal_content])
    return SimpleNamespace(
        output_parsed=None,  # no parsed object
        output=[message_item],
        usage=SimpleNamespace(
            input_tokens=100,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens=5,
        ),
    )


def _make_fake_client(fake_response: object) -> MagicMock:
    """Build a fake AsyncOpenAI client whose responses.parse is an AsyncMock."""
    fake_client = MagicMock()
    fake_client.responses.parse = AsyncMock(return_value=fake_response)
    return fake_client


@pytest.mark.asyncio
async def test_grade_communication_returns_level():
    verdict = CommunicationVerdict(
        evidence_quotes=["clear and structured"],
        justification="organized answers",
        level="strong",
    )
    fake_response = _make_fake_response(verdict, cached=800)
    fake_client = _make_fake_client(fake_response)

    with patch(
        "app.modules.reporting.scoring.judge.get_raw_openai_client",
        return_value=fake_client,
    ):
        result = await grade_communication(
            transcript_text="CANDIDATE: I structured my answer clearly...",
            correlation_id="c1",
        )

    assert result.level == "strong"

    # Confirm responses.parse was called (not chat.completions)
    fake_client.responses.parse.assert_called_once()
    call_kwargs = fake_client.responses.parse.call_args.kwargs
    assert call_kwargs["text_format"] is CommunicationVerdict
    assert "input" in call_kwargs
    assert "messages" not in call_kwargs  # old chat.completions key absent


@pytest.mark.asyncio
async def test_grade_communication_refusal_returns_weak_no_crash():
    """When the model refuses, grade_communication returns a conservative weak
    verdict rather than raising."""
    fake_response = _make_refusal_response()
    fake_client = _make_fake_client(fake_response)

    with patch(
        "app.modules.reporting.scoring.judge.get_raw_openai_client",
        return_value=fake_client,
    ):
        result = await grade_communication(
            transcript_text="CANDIDATE: I used Java.",
            correlation_id="c1",
        )

    assert result.level == "weak"
    assert result.evidence_quotes == []


@pytest.mark.asyncio
async def test_grade_communication_passes_reasoning_when_effort_set():
    """When report_scorer_effort is truthy, reasoning dict is passed to responses.parse."""
    verdict = CommunicationVerdict(
        evidence_quotes=[], justification="ok", level="adequate"
    )
    fake_response = _make_fake_response(verdict, cached=0)
    fake_client = _make_fake_client(fake_response)

    with (
        patch(
            "app.modules.reporting.scoring.judge.get_raw_openai_client",
            return_value=fake_client,
        ),
        patch(
            "app.modules.reporting.scoring.judge.ai_config",
        ) as mock_cfg,
    ):
        mock_cfg.report_scorer_model = "gpt-5.4"
        mock_cfg.report_scorer_effort = "medium"
        mock_cfg.report_scorer_prompt_version = "v3"
        mock_cfg.report_scorer_prompt_cache_key_prefix = "test"

        await grade_communication(
            transcript_text="CANDIDATE: I used Java.",
            correlation_id="c2",
        )

    call_kwargs = fake_client.responses.parse.call_args.kwargs
    assert "reasoning" in call_kwargs
    assert call_kwargs["reasoning"] == {"effort": "medium"}
    # Must NOT use the old reasoning_effort= keyword
    assert "reasoning_effort" not in call_kwargs

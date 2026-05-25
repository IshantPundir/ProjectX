"""Tests for the per-answer LLM judge.

Mocks at the app/ai boundary — no real LLM calls.
Patch target: ``app.modules.reporting.scoring.judge.get_raw_openai_client``

The fake response mirrors the real ``ParsedResponse`` accessor contract:
- ``response.output_parsed`` returns the parsed Pydantic model (fast path).
- ``response.usage`` has ``input_tokens_details.cached_tokens`` (new shape
  in the Responses API — differs from chat.completions' prompt_tokens_details).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.reporting.schemas import JudgeVerdict
from app.modules.reporting.scoring.judge import grade_answer


def _make_fake_response(verdict: object, cached: int) -> SimpleNamespace:
    """Build a fake ParsedResponse-shaped object.

    Mirrors the two fields judge.py reads:
    - ``output_parsed``  — the parsed Pydantic model (SDK convenience property).
    - ``usage``          — token counters (Responses API shape).
    """
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


QUESTION = {
    "id": "q6",
    "text": "Java/JSON transform?",
    "rubric": {
        "excellent": "validates + maps",
        "meets_bar": "basic transform",
        "below_bar": "vague, no validation",
    },
    "positive_evidence": ["schema validation"],
    "red_flags": ["no validation"],
}


@pytest.mark.asyncio
async def test_grade_answer_grounds_evidence_and_returns_level():
    verdict = JudgeVerdict(
        evidence_quotes=["take this up at Java"],
        red_flags_hit=["buzzwords"],
        justification="thin",
        level="below_bar",
    )
    fake_response = _make_fake_response(verdict, cached=800)
    fake_client = _make_fake_client(fake_response)

    with patch(
        "app.modules.reporting.scoring.judge.get_raw_openai_client",
        return_value=fake_client,
    ):
        rating = await grade_answer(
            question=QUESTION,
            transcript_excerpt="CANDIDATE: I would take this up at Java...",
            correlation_id="c1",
        )

    assert rating.question_id == "q6"
    assert rating.level == "below_bar"
    assert rating.evidence_quotes == ["take this up at Java"]  # grounded (substring present)
    assert rating.grounded is True

    # Confirm responses.parse was called (not chat.completions)
    fake_client.responses.parse.assert_called_once()
    call_kwargs = fake_client.responses.parse.call_args.kwargs
    assert call_kwargs["text_format"] is JudgeVerdict
    assert "input" in call_kwargs
    assert "messages" not in call_kwargs  # old chat.completions key absent


@pytest.mark.asyncio
async def test_grade_answer_drops_hallucinated_quote():
    verdict = JudgeVerdict(
        evidence_quotes=["I led a 200-person team"],  # NOT in transcript
        red_flags_hit=[],
        justification="x",
        level="meets_bar",
    )
    fake_response = _make_fake_response(verdict, cached=0)
    fake_client = _make_fake_client(fake_response)

    with patch(
        "app.modules.reporting.scoring.judge.get_raw_openai_client",
        return_value=fake_client,
    ):
        rating = await grade_answer(
            question=QUESTION,
            transcript_excerpt="CANDIDATE: I used Java.",
            correlation_id="c1",
        )

    assert rating.evidence_quotes == []  # hallucinated quote dropped
    assert rating.grounded is False  # flagged: an ungrounded quote was returned


@pytest.mark.asyncio
async def test_grade_answer_refusal_returns_below_bar_no_crash():
    """When the model returns a refusal, grade_answer returns a conservative
    below_bar AnswerRating with no evidence and grounded=False.  It must NOT
    raise an exception (one refusal must not abort the entire report)."""
    fake_response = _make_refusal_response()
    fake_client = _make_fake_client(fake_response)

    with patch(
        "app.modules.reporting.scoring.judge.get_raw_openai_client",
        return_value=fake_client,
    ):
        rating = await grade_answer(
            question=QUESTION,
            transcript_excerpt="CANDIDATE: I used Java.",
            correlation_id="c1",
        )

    assert rating.question_id == "q6"
    assert rating.level == "below_bar"
    assert rating.evidence_quotes == []
    assert rating.grounded is False


@pytest.mark.asyncio
async def test_grade_answer_passes_reasoning_when_effort_set(monkeypatch):
    """When report_scorer_effort is truthy, reasoning dict is passed to responses.parse."""
    from app.ai import config as ai_config_module

    monkeypatch.setattr(ai_config_module.ai_config, "_settings", None)  # noqa
    # Override just the effort property via a lightweight approach
    verdict = JudgeVerdict(
        evidence_quotes=[],
        red_flags_hit=[],
        justification="ok",
        level="meets_bar",
    )
    fake_response = _make_fake_response(verdict, cached=0)
    fake_client = _make_fake_client(fake_response)

    # Patch ai_config.report_scorer_effort to return a truthy value
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

        await grade_answer(
            question=QUESTION,
            transcript_excerpt="CANDIDATE: I used Java.",
            correlation_id="c2",
        )

    call_kwargs = fake_client.responses.parse.call_args.kwargs
    assert "reasoning" in call_kwargs
    assert call_kwargs["reasoning"] == {"effort": "medium"}
    # Must NOT use the old reasoning_effort= keyword
    assert "reasoning_effort" not in call_kwargs

"""Tests for the per-answer LLM judge (Task 14).

Mocks at the app/ai boundary — no real LLM calls.
Patch target: ``app.modules.reporting.scoring.judge.get_openai_client``
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.reporting.schemas import JudgeVerdict
from app.modules.reporting.scoring.judge import grade_answer


def _completion(cached: int):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
            prompt_tokens=900,
            completion_tokens=40,
        )
    )


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
    fake_client = AsyncMock()
    fake_client.chat.completions.create_with_completion = AsyncMock(
        return_value=(verdict, _completion(800))
    )
    with patch(
        "app.modules.reporting.scoring.judge.get_openai_client",
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


@pytest.mark.asyncio
async def test_grade_answer_drops_hallucinated_quote():
    verdict = JudgeVerdict(
        evidence_quotes=["I led a 200-person team"],  # NOT in transcript
        red_flags_hit=[],
        justification="x",
        level="meets_bar",
    )
    fake_client = AsyncMock()
    fake_client.chat.completions.create_with_completion = AsyncMock(
        return_value=(verdict, _completion(0))
    )
    with patch(
        "app.modules.reporting.scoring.judge.get_openai_client",
        return_value=fake_client,
    ):
        rating = await grade_answer(
            question=QUESTION,
            transcript_excerpt="CANDIDATE: I used Java.",
            correlation_id="c1",
        )
    assert rating.evidence_quotes == []  # hallucinated quote dropped
    assert rating.grounded is False  # flagged: an ungrounded quote was returned

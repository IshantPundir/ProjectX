import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.scoring.narrative import write_narrative
from app.modules.reporting.schemas import (
    NarrativeOut, DecisionOut, WhyColumn, MethodologyOut,
)


def _out():
    return NarrativeOut(
        decision=DecisionOut(headline="Borderline.",
                             why_positive=WhyColumn(title="Foundations", body="Meets experience."),
                             why_negative=WhyColumn(title="Depth", body="Thin technical answers.")),
        quick_summary="Sits on the line.",
        strengths=[], concerns=[], questions=[],
        methodology=MethodologyOut(note="7 of 8 questions.", charity_flags=[]))


@pytest.mark.asyncio
async def test_write_narrative_returns_prose():
    class R:
        output_parsed = _out()
        usage = None
    with patch("app.modules.reporting.scoring.narrative.get_raw_openai_client") as gc:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=R())
        gc.return_value = client
        res = await write_narrative(ground_truth_json="{}", correlation_id="c1")
    assert res.decision.headline == "Borderline."
    assert res.methodology.note.startswith("7 of 8")


@pytest.mark.asyncio
async def test_write_narrative_refusal_returns_valid_fallback():
    class R:
        output_parsed = None
        output = []
        usage = None
    with patch("app.modules.reporting.scoring.narrative.get_raw_openai_client") as gc:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=R())
        gc.return_value = client
        res = await write_narrative(ground_truth_json="{}", correlation_id="c1")
    # fallback is a valid NarrativeOut, not an exception
    assert isinstance(res, NarrativeOut)
    assert res.decision.headline  # non-empty fallback headline
    assert res.methodology.note == "Narrative generation failed."
    assert res.strengths == [] and res.concerns == []

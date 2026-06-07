import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.scoring.aggregate import ScoredSignal
from app.modules.reporting.scoring.holistic import score_holistic
from app.modules.reporting.schemas import HolisticAdjustmentOut


def _s(level, score):
    return ScoredSignal(value="a", type="competency", weight=1, knockout=False,
                        priority="preferred", level=level, score=score)


@pytest.mark.asyncio
async def test_secondary_breadth_passed_to_prompt():
    captured = {}
    async def _parse(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"output_parsed": HolisticAdjustmentOut(delta=3, justification="breadth")})()
    with patch("app.modules.reporting.scoring.holistic.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(side_effect=_parse)
        out = await score_holistic(
            session_score=70, scored=[_s("solid", 80)], is_knockout_close=False,
            coverage=0.8, transcript_text="...", demonstrated_secondaries=["kubernetes", "graphql"],
            correlation_id="cid")
    assert out.delta == 3
    joined = "".join(str(m["content"]) for m in captured["input"])
    assert "kubernetes" in joined and "graphql" in joined


@pytest.mark.asyncio
async def test_none_session_score_skips():
    out = await score_holistic(session_score=None, scored=[], is_knockout_close=False,
                               coverage=0.0, transcript_text="", demonstrated_secondaries=[],
                               correlation_id="cid")
    assert out.delta == 0

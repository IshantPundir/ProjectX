import pytest
from unittest.mock import AsyncMock, patch
from app.modules.reporting.scoring.holistic import score_holistic
from app.modules.reporting.schemas import HolisticAdjustmentOut

class _Resp:
    def __init__(self, parsed): self.output_parsed = parsed; self.usage = None

@pytest.mark.asyncio
async def test_score_holistic_bounds_delta_and_grounds_quotes():
    parsed = HolisticAdjustmentOut(
        evidence_quotes=["I just used the library"], justification="pervasive surface answers", delta=-99)
    client = AsyncMock()
    client.responses.parse = AsyncMock(return_value=_Resp(parsed))
    with patch("app.modules.reporting.scoring.holistic.get_raw_openai_client", return_value=client):
        out = await score_holistic(
            session_score=55, scored=[], knockout_close=False, coverage=0.8,
            transcript_text="... I just used the library ...", correlation_id="c1")
    assert out.delta == -5                       # hard-bounded
    assert out.evidence_quotes == ["I just used the library"]   # grounded substring kept

@pytest.mark.asyncio
async def test_score_holistic_refusal_returns_zero_delta():
    client = AsyncMock()
    client.responses.parse = AsyncMock(return_value=_Resp(None))
    with patch("app.modules.reporting.scoring.holistic.get_raw_openai_client", return_value=client):
        out = await score_holistic(session_score=55, scored=[], knockout_close=False,
                                   coverage=0.8, transcript_text="x", correlation_id="c1")
    assert out.delta == 0

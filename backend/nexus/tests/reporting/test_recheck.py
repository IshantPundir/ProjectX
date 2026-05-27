import pytest
from unittest.mock import AsyncMock, patch

from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.types import SignalDef, SignalTurn


def _fake_response(out: SignalRecheckOut):
    class R:
        output_parsed = out
        usage = None
    return R()


@pytest.mark.asyncio
async def test_recheck_grounds_quotes_and_records_override():
    sig = SignalDef("API expertise", "competency", 3, knockout=False, priority="required")
    turns = [SignalTurn(candidate_quote="I have built REST connectors end to end",
                        grade="thin", reasoning="r", question_id="q1")]
    model_out = SignalRecheckOut(
        evidence_quotes=["built REST connectors end to end", "totally made up span"],
        justification="Names a concrete mechanism.", grade="concrete",
        state="sufficient", overridden=True, override_reason="Full context shows depth.")
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as gc:
        client = AsyncMock()
        client.responses.parse = AsyncMock(return_value=_fake_response(model_out))
        gc.return_value = client
        res = await recheck_signal(signal_def=sig, evidence_turns=turns,
                                   question_context="Q: build a connector?\nrubric: ...",
                                   engine_state="partial", correlation_id="c1")
    assert res.state == "sufficient"
    assert res.overridden is True
    assert "totally made up span" not in res.evidence_quotes   # ungrounded dropped
    assert "built REST connectors end to end" in res.evidence_quotes


@pytest.mark.asyncio
async def test_recheck_refusal_falls_back_to_engine_state():
    sig = SignalDef("X", "competency", 2, knockout=False, priority="required")
    turns = [SignalTurn(candidate_quote="q", grade="thin", reasoning="r", question_id="q1")]
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as gc:
        client = AsyncMock()
        class R:
            output_parsed = None
            output = []
            usage = None
        client.responses.parse = AsyncMock(return_value=R())
        gc.return_value = client
        res = await recheck_signal(signal_def=sig, evidence_turns=turns,
                                   question_context="ctx", engine_state="partial",
                                   correlation_id="c1")
    assert res.state == "partial"        # unchanged
    assert res.overridden is False

import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import EvidenceNote, TimeSpan
from app.modules.reporting.schemas import SignalRecheckOut
from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.scoring.types import SignalDef


def _note(texture, stance="supports"):
    return EvidenceNote(seq=1, turn_ref="t-1", signal="python", stance=stance, texture=texture,
                        quote="I built a Python ETL pipeline handling 2M rows/day",
                        span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1", via_probe=False)


@pytest.mark.asyncio
async def test_recheck_can_override_level():
    fake = SignalRecheckOut(evidence_quotes=["I built a Python ETL pipeline handling 2M rows/day"],
                            justification="tradeoffs shown", level="strong",
                            overridden=True, override_reason="depth evident")
    resp = type("R", (), {"output_parsed": fake})()
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(return_value=resp)
        out = await recheck_signal(
            signal_def=SignalDef(value="python", type="competency", weight=3,
                                 knockout=True, priority="required"),
            notes=[_note("concrete")], question_context="Q: ...\nrubric: {}",
            engine_level="solid", correlation_id="cid")
    assert out.level == "strong"


@pytest.mark.asyncio
async def test_recheck_passes_question_kind_into_prompt():
    fake = SignalRecheckOut(evidence_quotes=[], justification="ok", level="solid")
    resp = type("R", (), {"output_parsed": fake})()
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(return_value=resp)
        await recheck_signal(
            signal_def=SignalDef(value="years", type="experience", weight=3,
                                 knockout=True, priority="required"),
            notes=[_note("thin")], question_context="Q: how many years?\nrubric: {}",
            engine_level="solid", correlation_id="cid", question_kind="experience_check")
        kwargs = c.return_value.responses.parse.call_args.kwargs
    system_msg = next(m["content"] for m in kwargs["input"] if m["role"] == "system")
    assert "experience_check" in system_msg
    assert "question_kind" in system_msg


@pytest.mark.asyncio
async def test_recheck_refusal_keeps_engine_level():
    resp = type("R", (), {"output_parsed": None})()
    with patch("app.modules.reporting.scoring.recheck.get_raw_openai_client") as c:
        c.return_value.responses.parse = AsyncMock(return_value=resp)
        out = await recheck_signal(
            signal_def=SignalDef(value="python", type="competency", weight=3,
                                 knockout=True, priority="required"),
            notes=[_note("thin")], question_context="ctx", engine_level="thin",
            correlation_id="cid")
    assert out.level == "thin"
    assert out.overridden is False

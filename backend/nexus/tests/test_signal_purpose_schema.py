import pytest
from pydantic import ValidationError
from app.ai.schemas import SignalItemV2, ExtractedSignals


def _sig(value="Workato workflow development", purpose="skill", **kw):
    base = dict(value=value, type="competency", priority="required", weight=3,
                knockout=False, stage="interview", source="ai_extracted",
                inference_basis=None, purpose=purpose)
    base.update(kw)
    return SignalItemV2(**base)


def test_purpose_defaults_to_skill_when_absent():
    s = SignalItemV2(value="x", type="competency", priority="required", weight=2,
                     knockout=False, stage="interview", source="ai_extracted",
                     inference_basis=None)
    assert s.purpose == "skill"


def test_purpose_accepts_eligibility():
    assert _sig(purpose="eligibility").purpose == "eligibility"


def test_purpose_rejects_unknown():
    with pytest.raises(ValidationError):
        _sig(purpose="made_up")


def test_extracted_signals_requires_at_least_one_skill():
    elig = dict(type="experience", priority="required", weight=3, knockout=True,
                stage="screen", source="ai_extracted", inference_basis=None,
                purpose="eligibility")
    sigs = [SignalItemV2(value=f"{i}+ years", **elig) for i in range(5)]
    with pytest.raises(ValidationError):
        ExtractedSignals(signals=sigs, seniority_level="mid", role_summary="a role summary")


def test_extracted_signals_passes_with_a_skill():
    elig = dict(type="experience", priority="required", weight=3, knockout=True,
                stage="screen", source="ai_extracted", inference_basis=None,
                purpose="eligibility")
    sigs = [SignalItemV2(value=f"{i}+ years", **elig) for i in range(4)]
    sigs.append(_sig())  # one skill (competency/interview)
    out = ExtractedSignals(signals=sigs, seniority_level="mid", role_summary="a role summary")
    assert any(s.purpose == "skill" for s in out.signals)

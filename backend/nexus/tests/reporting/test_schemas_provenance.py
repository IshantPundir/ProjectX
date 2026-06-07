from app.modules.reporting.schemas import SignalAssessmentOut


def test_signal_assessment_has_provenance():
    s = SignalAssessmentOut(signal="python", type="competency", weight=3, knockout=True,
                            priority="required", provenance="asked_directly",
                            level="solid", score=80, evidence=[], overridden=False,
                            override_reason=None)
    assert s.provenance == "asked_directly"
    assert s.level == "solid"

from app.modules.question_bank.service import _signals_for_generation


def _s(value, purpose="skill", type_="competency"):
    return {"value": value, "type": type_, "purpose": purpose, "weight": 3,
            "priority": "required", "knockout": False, "stage": "interview"}


def test_ai_screening_drops_eligibility():
    sigs = [_s("Workato"), _s("4+ years", purpose="eligibility", type_="experience"),
            _s("BTech", purpose="eligibility", type_="credential")]
    out = _signals_for_generation(sigs, stage_type="ai_screening")
    assert [s["value"] for s in out] == ["Workato"]


def test_legacy_signals_without_purpose_default_skill():
    sigs = [{"value": "Workato", "type": "competency", "weight": 3,
             "priority": "required", "knockout": False, "stage": "interview"}]
    out = _signals_for_generation(sigs, stage_type="ai_screening")
    assert [s["value"] for s in out] == ["Workato"]


def test_phone_screen_keeps_eligibility():
    sigs = [_s("Workato"), _s("4+ years", purpose="eligibility", type_="experience")]
    out = _signals_for_generation(sigs, stage_type="phone_screen")
    assert len(out) == 2

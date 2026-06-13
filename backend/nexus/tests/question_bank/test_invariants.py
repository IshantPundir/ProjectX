from app.modules.question_bank.invariants import check_bank_invariants, Violation
from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric, FollowUpDimension


def _q(kind, mins=4.0, signals=("Workato workflow development",), pos=0, mand=False):
    return GeneratedQuestion(
        position=pos, text="Walk me through a Workato workflow you designed.",
        primary_signal=signals[0], signal_values=list(signals), estimated_minutes=mins,
        is_mandatory=mand,
        follow_ups=[FollowUpDimension(dimension="d", intent="i",
                    seed_probe="What did you choose it over?", listen_for=["a tradeoff"])],
        positive_evidence=["a", "b", "c"], red_flags=["says we", "no tradeoff"],
        rubric=QuestionRubric(excellent="x" * 20, meets_bar="y" * 20, below_bar="z" * 20),
        evaluation_hint="tests skill depth", question_kind=kind,
    )


def _sig(value, weight=3, purpose="skill"):
    return {"value": value, "weight": weight, "purpose": purpose, "type": "competency"}


def test_two_project_deepdives_flagged():
    qs = [_q("project_deepdive", pos=0), _q("project_deepdive", pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "too_many_project_deepdive" and v.hard_repairable for v in vs)


def test_forbidden_kinds_flagged():
    qs = [_q("experience_check"), _q("compliance_binary")]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "forbidden_kind" and v.hard_repairable for v in vs)


def test_two_behavioral_flagged():
    qs = [_q("behavioral", pos=0), _q("behavioral", pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "too_many_behavioral" for v in vs)


def test_over_budget_flagged():
    qs = [_q("technical_scenario", mins=15.0), _q("technical_scenario", mins=15.0)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=[])
    assert any(v.code == "over_budget" for v in vs)


def test_uncovered_high_weight_skill_detected_not_repairable():
    qs = [_q("technical_scenario", signals=("Workato workflow development",))]
    signals = [_sig("Workato workflow development", 3), _sig("AI-driven workflows", 3)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, signals=signals)
    cov = [v for v in vs if v.code == "uncovered_high_weight_skill"]
    assert cov and cov[0].hard_repairable is False
    assert "AI-driven workflows" in cov[0].description


def test_clean_ai_screen_has_no_violations():
    qs = [_q("technical_scenario", mins=4.0, signals=("Workato workflow development",)),
          _q("project_deepdive", mins=4.0, signals=("Workato workflow development",))]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20,
                               signals=[_sig("Workato workflow development", 3)])
    assert vs == []


def test_non_ai_screening_stage_no_rules():
    qs = [_q("project_deepdive"), _q("project_deepdive"), _q("experience_check")]
    vs = check_bank_invariants(qs, stage_type="phone_screen", stage_duration_minutes=10, signals=[])
    assert vs == []

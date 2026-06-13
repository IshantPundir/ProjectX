from app.modules.question_bank.invariants import check_bank_invariants, Violation, hard_repair
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


def test_hard_repair_caps_project_deepdive_to_one_keeps_mandatory():
    qs = [_q("project_deepdive", pos=0, mand=False), _q("project_deepdive", pos=1, mand=True)]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20)
    dds = [q for q in out if q.question_kind == "project_deepdive"]
    assert len(dds) == 1 and dds[0].is_mandatory is True  # kept the mandatory one
    assert [q.position for q in out] == list(range(len(out)))  # re-packed


def test_hard_repair_drops_forbidden_kinds():
    qs = [_q("technical_scenario"), _q("experience_check"), _q("compliance_binary")]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20)
    assert all(q.question_kind not in ("experience_check", "compliance_binary") for q in out)


def test_hard_repair_trims_to_budget_keeps_mandatory():
    qs = [_q("technical_scenario", mins=8.0, pos=0, mand=True),
          _q("technical_scenario", mins=8.0, pos=1),
          _q("technical_scenario", mins=8.0, pos=2)]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20)
    assert sum(float(q.estimated_minutes) for q in out) <= 20
    assert any(q.is_mandatory for q in out)  # mandatory survived the trim


def test_hard_repair_idempotent_on_clean_bank():
    qs = [_q("technical_scenario", mins=4.0), _q("project_deepdive", mins=4.0)]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20)
    assert len(out) == 2


def test_hard_repair_noop_for_phone_screen_keeps_experience_check():
    qs = [_q("experience_check", pos=0), _q("compliance_binary", pos=1),
          _q("project_deepdive", pos=2), _q("project_deepdive", pos=3)]
    out = hard_repair(qs, stage_type="phone_screen", stage_duration_minutes=20)
    kinds = [q.question_kind for q in out]
    assert "experience_check" in kinds and "compliance_binary" in kinds  # NOT stripped
    assert kinds.count("project_deepdive") == 2  # phone_screen has no deepdive cap
    assert len(out) == 4  # unchanged

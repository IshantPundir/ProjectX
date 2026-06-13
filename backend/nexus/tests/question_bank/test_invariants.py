from app.modules.question_bank.invariants import check_bank_invariants, Violation, hard_repair, seniority_requires_deepdive
from app.modules.question_bank.schemas import GeneratedQuestion, QuestionRubric, FollowUpDimension
from app.modules.question_bank.coverage_planner import CoveragePlan


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


def test_two_project_deepdives_flagged():
    qs = [_q("project_deepdive", pos=0), _q("project_deepdive", pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, plan=None)
    assert any(v.code == "too_many_project_deepdive" and v.hard_repairable for v in vs)


def test_forbidden_kinds_flagged():
    qs = [_q("experience_check"), _q("compliance_binary")]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, plan=None)
    assert any(v.code == "forbidden_kind" and v.hard_repairable for v in vs)


def test_two_behavioral_flagged():
    qs = [_q("behavioral", pos=0), _q("behavioral", pos=1)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, plan=None)
    assert any(v.code == "too_many_behavioral" for v in vs)


def test_over_budget_flagged():
    qs = [_q("technical_scenario", mins=15.0), _q("technical_scenario", mins=15.0)]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20, plan=None)
    assert any(v.code == "over_budget" for v in vs)


def test_uncovered_required_primary_detected_not_repairable():
    qs = [_q("technical_scenario", signals=("Workato workflow development",))]
    plan = CoveragePlan(
        slot_budget=6, must_cover_count=2,
        required_primaries=["Workato workflow development", "AI-driven workflows"],
    )
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    cov = [v for v in vs if v.code == "uncovered_required_primary"]
    assert cov and cov[0].hard_repairable is False
    assert "AI-driven workflows" in cov[0].description


def test_covered_required_primary_via_primary_signal_no_violation():
    # The skill is the question's PRIMARY_SIGNAL -> covered (scored).
    qs = [_q("technical_scenario", signals=("Workato workflow development",)),
          _q("project_deepdive", signals=("Workato workflow development",))]
    plan = CoveragePlan(slot_budget=6, must_cover_count=1,
                        required_primaries=["Workato workflow development"])
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    assert vs == []


def test_required_primary_only_in_signal_values_is_NOT_covered():
    # Skill rides as a SECONDARY (in signal_values, not primary_signal) -> still uncovered
    # because the report scores primary_signal only.
    qs = [_q("technical_scenario",
             signals=("Workato workflow development", "AI-driven workflows"))]
    # primary_signal == signals[0] == "Workato workflow development" (see _q)
    plan = CoveragePlan(slot_budget=6, must_cover_count=1,
                        required_primaries=["AI-driven workflows"])
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=plan)
    assert any(v.code == "uncovered_required_primary" for v in vs)


def test_plan_none_skips_coverage_check():
    qs = [_q("technical_scenario")]
    vs = check_bank_invariants(qs, stage_type="ai_screening",
                               stage_duration_minutes=20, plan=None)
    assert all(v.code != "uncovered_required_primary" for v in vs)


def test_non_ai_screening_stage_no_rules():
    qs = [_q("project_deepdive"), _q("project_deepdive"), _q("experience_check")]
    vs = check_bank_invariants(qs, stage_type="phone_screen", stage_duration_minutes=10, plan=None)
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


def test_hard_repair_coverage_aware_never_drops_sole_required_primary():
    # 3 x 8min = 24min over a 20min budget. The required-primary's question is the
    # SOLE cover of "must" and must survive even though it's last/non-mandatory.
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("opt1",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("opt2",)),
          _q("technical_scenario", mins=8.0, pos=2, signals=("must",))]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20,
                      required_primaries={"must"})
    assert any(q.primary_signal == "must" for q in out)  # protected
    assert sum(float(q.estimated_minutes) for q in out) <= 20


def test_hard_repair_drops_optional_primary_first():
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("must",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("opt",)),
          _q("technical_scenario", mins=8.0, pos=2, signals=("must",))]
    # "opt" is not a required_primary -> Pass 1 drops it before any "must" question.
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20,
                      required_primaries={"must"})
    assert all(q.primary_signal != "opt" for q in out)


def test_hard_repair_drops_redundant_required_primary_in_pass2():
    # No non-required-primary questions remain; Pass 1 finds nothing.
    # Pass 2 drops the redundant second cover of "must".
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("must",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("must",))]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=12,
                      required_primaries={"must"})
    assert len(out) == 1
    assert out[0].primary_signal == "must"


# ---------------------------------------------------------------------------
# Seniority-gated project_deepdive floor tests
# ---------------------------------------------------------------------------

def test_seniority_requires_deepdive_senior_lead_principal():
    assert seniority_requires_deepdive("senior") is True
    assert seniority_requires_deepdive("lead") is True
    assert seniority_requires_deepdive("principal") is True
    assert seniority_requires_deepdive("PRINCIPAL") is True  # case-insensitive


def test_seniority_requires_deepdive_junior_mid_none():
    assert seniority_requires_deepdive("junior") is False
    assert seniority_requires_deepdive("mid") is False
    assert seniority_requires_deepdive(None) is False
    assert seniority_requires_deepdive("") is False


def test_too_few_project_deepdive_fires_for_senior_when_zero():
    qs = [_q("technical_scenario", signals=("A",)), _q("technical_scenario", signals=("B",))]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20,
                               plan=None, require_deepdive=True)
    few = [v for v in vs if v.code == "too_few_project_deepdive"]
    assert few and few[0].hard_repairable is False


def test_too_few_project_deepdive_not_fired_when_one_present():
    qs = [_q("technical_scenario", signals=("A",)), _q("project_deepdive", signals=("B",))]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20,
                               plan=None, require_deepdive=True)
    assert all(v.code != "too_few_project_deepdive" for v in vs)


def test_too_few_project_deepdive_not_fired_when_not_required():
    qs = [_q("technical_scenario", signals=("A",)), _q("technical_scenario", signals=("B",))]
    vs = check_bank_invariants(qs, stage_type="ai_screening", stage_duration_minutes=20,
                               plan=None, require_deepdive=False)
    assert all(v.code != "too_few_project_deepdive" for v in vs)


def test_hard_repair_protects_sole_deepdive_for_senior_over_budget():
    # 3 x 8min = 24 > 20. All three have primary 'A' (the deepdive's primary is redundant).
    # Without protection the reversed scan would drop the deepdive first; require_deepdive
    # must keep it and drop a redundant scenario instead.
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("A",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("A",)),
          _q("project_deepdive", mins=8.0, pos=2, signals=("A",))]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20,
                      required_primaries={"A"}, require_deepdive=True)
    assert any(q.question_kind == "project_deepdive" for q in out)  # deepdive survived
    assert sum(float(q.estimated_minutes) for q in out) <= 20


def test_hard_repair_does_not_force_protect_deepdive_when_not_required():
    # require_deepdive=False → normal behavior (deepdive not specially protected).
    qs = [_q("technical_scenario", mins=8.0, pos=0, signals=("A",)),
          _q("technical_scenario", mins=8.0, pos=1, signals=("A",)),
          _q("project_deepdive", mins=8.0, pos=2, signals=("A",))]
    out = hard_repair(qs, stage_type="ai_screening", stage_duration_minutes=20,
                      required_primaries={"A"}, require_deepdive=False)
    assert sum(float(q.estimated_minutes) for q in out) <= 20  # still fits; no crash

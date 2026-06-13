from app.modules.question_bank.coverage_planner import build_coverage_plan, CoveragePlan


def _sig(value, *, weight=2, priority="preferred", purpose="skill"):
    return {"value": value, "weight": weight, "priority": priority,
            "purpose": purpose, "type": "competency"}


def test_feasible_all_must_cover_become_primaries():
    signals = [_sig("A", weight=3), _sig("B", weight=2), _sig("C", priority="required", weight=2)]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.slot_budget == 6
    assert set(plan.required_primaries) == {"A", "B", "C"}
    assert plan.secondary_only == []
    assert plan.dropped == []
    assert plan.feasible is True
    assert plan.recommended_minutes == 20


def test_optional_tail_is_bundle_eligible_not_dropped_when_feasible():
    signals = [_sig("A", weight=3), _sig("opt", weight=1, priority="preferred")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert "A" in plan.required_primaries
    assert "opt" in plan.bundle_eligible
    assert plan.dropped == []
    assert plan.feasible is True


def test_over_subscription_overflow_must_covers_are_secondary_only():
    # 8 must-covers, slot_budget = floor(15/3) = 5 -> 3 overflow
    signals = [_sig(f"S{i}", weight=3) for i in range(8)]
    plan = build_coverage_plan(signals, stage_duration_minutes=15, min_per_scored_slot=3.0)
    assert plan.slot_budget == 5
    assert len(plan.required_primaries) == 5
    assert len(plan.secondary_only) == 3
    # overflow must-covers ride in bundle_eligible too (LLM folds where coherent)
    assert set(plan.secondary_only).issubset(set(plan.bundle_eligible))
    assert plan.feasible is False
    assert plan.recommended_minutes == 24  # ceil(8 * 3.0)


def test_ranking_prefers_required_then_weight():
    # required beats preferred; within priority, higher weight first
    signals = [
        _sig("low", weight=2, priority="preferred"),
        _sig("req1", weight=2, priority="required"),
        _sig("req3", weight=3, priority="required"),
    ]
    # slot_budget = floor(3/3) = 1 -> only the top-ranked survives as primary
    plan = build_coverage_plan(signals, stage_duration_minutes=3, min_per_scored_slot=3.0)
    assert plan.required_primaries == ["req3"]
    assert set(plan.secondary_only) == {"req1", "low"}


def test_eligibility_signals_are_ignored():
    signals = [_sig("skill", weight=2), _sig("years", weight=3, purpose="eligibility")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.required_primaries == ["skill"]
    assert "years" not in plan.required_primaries
    assert "years" not in plan.bundle_eligible


def test_legacy_signal_missing_metadata_is_must_cover():
    # No weight / priority keys -> weight defaults to 2, priority -> preferred,
    # purpose -> skill. weight==2 makes it must-cover (conservative, no silent drop).
    plan = build_coverage_plan([{"value": "legacy"}], stage_duration_minutes=20,
                               min_per_scored_slot=3.0)
    assert plan.required_primaries == ["legacy"]


def test_preferred_weight1_is_optional_tail():
    signals = [_sig("must", weight=2), _sig("opt", weight=1, priority="preferred")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.required_primaries == ["must"]
    assert "opt" in plan.bundle_eligible


def test_zero_must_cover_is_feasible_empty():
    signals = [_sig("opt", weight=1, priority="preferred")]
    plan = build_coverage_plan(signals, stage_duration_minutes=20, min_per_scored_slot=3.0)
    assert plan.required_primaries == []
    assert plan.feasible is True


def test_slot_budget_floor_at_least_one():
    plan = build_coverage_plan([_sig("A")], stage_duration_minutes=1, min_per_scored_slot=3.0)
    assert plan.slot_budget == 1


def test_report_is_human_readable_string():
    signals = [_sig(f"S{i}", weight=3) for i in range(8)]
    plan = build_coverage_plan(signals, stage_duration_minutes=15, min_per_scored_slot=3.0)
    assert isinstance(plan.report, str) and plan.report
    assert "secondary" in plan.report.lower() or "extend" in plan.report.lower()


def test_exact_fit_is_feasible():
    # must_cover_count == slot_budget (floor(15/3)=5) -> feasible, no overflow.
    signals = [_sig(f"S{i}") for i in range(5)]
    plan = build_coverage_plan(signals, stage_duration_minutes=15, min_per_scored_slot=3.0)
    assert plan.feasible is True
    assert len(plan.required_primaries) == 5
    assert plan.secondary_only == []

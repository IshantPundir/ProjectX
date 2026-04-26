"""Edit-category classifier tests — A/B/C/D mapping per spec §8."""
import pytest

from app.modules.pipelines.classifier import classify_pipeline_diff, EditCategory


def _stage(id_, position, stage_type, **overrides):
    base = {
        "id": id_, "position": position, "stage_type": stage_type,
        "name": f"S{position}", "paused_at": None,
        "duration_minutes": 30 if stage_type not in ("intake", "debrief") else None,
        "difficulty": "medium" if stage_type not in ("intake", "debrief") else None,
        "signal_filter": {"include_types": ["competency"]} if stage_type not in ("intake", "debrief") else None,
        "pass_criteria": {"type": "all_knockouts_pass"},
        "advance_behavior": "auto_advance",
        "sla_days": None,
    }
    base.update(overrides)
    return base


def test_no_changes_is_category_a():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.A


def test_duration_change_is_category_a():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen", duration_minutes=45), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.A


def test_add_stage_is_category_b():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"),
                _stage("new", 2, "ai_screening"), _stage("s2", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B


def test_reorder_is_category_b():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"),
               _stage("s2", 2, "ai_screening"), _stage("s3", 3, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "ai_screening"),
                _stage("s1", 2, "phone_screen"), _stage("s3", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B


def test_remove_stage_with_zero_in_flight_is_category_c():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={"s1": 0})
    assert result.category == EditCategory.C
    assert result.in_flight.get("s1", 0) == 0


def test_remove_stage_with_in_flight_is_category_c_with_in_flight_count():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s2", 1, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={"s1": 3})
    assert result.category == EditCategory.C
    assert result.in_flight["s1"] == 3


def test_pause_is_category_c():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen", paused_at="2026-04-26T10:00:00Z"),
                _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.C


def test_stage_type_change_is_category_d():
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"), _stage("s1", 1, "ai_screening"), _stage("s2", 2, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.D


def test_highest_category_wins():
    """If a diff contains both A and B changes, B wins."""
    current = [_stage("s0", 0, "intake"), _stage("s1", 1, "phone_screen"), _stage("s2", 2, "debrief")]
    proposed = [_stage("s0", 0, "intake"),
                _stage("s1", 1, "phone_screen", duration_minutes=45),
                _stage("new", 2, "ai_screening"),
                _stage("s2", 3, "debrief")]
    result = classify_pipeline_diff(current=current, proposed=proposed, in_flight={})
    assert result.category == EditCategory.B  # B wins over A

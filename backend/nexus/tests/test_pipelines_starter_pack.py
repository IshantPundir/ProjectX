"""Tests for the starter pack — shape and integrity."""

import pytest

from app.modules.pipelines.starter_pack import STARTER_TEMPLATES, SYSTEM_FALLBACK_STARTER


EXPECTED_KEYS = {
    "standard_technical",
    "fast_track",
    "screening_only",
    "senior_leadership",
    "sales_commercial",
    "volume_hiring",
}


def test_starter_pack_has_six_templates():
    assert set(STARTER_TEMPLATES.keys()) == EXPECTED_KEYS


def test_system_fallback_is_in_pack():
    assert SYSTEM_FALLBACK_STARTER in STARTER_TEMPLATES


def test_every_template_has_required_fields():
    for key, tpl in STARTER_TEMPLATES.items():
        assert "name" in tpl, f"{key} missing name"
        assert "description" in tpl, f"{key} missing description"
        assert "stages" in tpl, f"{key} missing stages"
        assert len(tpl["stages"]) >= 1, f"{key} has no stages"


def test_every_stage_has_required_fields():
    required = {
        "position", "name", "stage_type", "duration_minutes",
        "difficulty", "signal_filter", "pass_criteria", "advance_behavior",
    }
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            missing = required - set(stage.keys())
            assert not missing, f"{key} stage {stage.get('position')} missing {missing}"


def test_stage_positions_are_sequential():
    for key, tpl in STARTER_TEMPLATES.items():
        positions = [s["position"] for s in tpl["stages"]]
        assert positions == list(range(len(positions))), f"{key} positions not sequential: {positions}"


def test_stage_types_are_valid():
    valid = {"phone_screen", "ai_interview", "human_interview", "panel_interview", "take_home"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            assert stage["stage_type"] in valid, f"{key} has invalid stage_type: {stage['stage_type']}"


def test_difficulties_are_valid():
    valid = {"easy", "medium", "hard"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            assert stage["difficulty"] in valid


def test_advance_behaviors_are_valid():
    valid = {"auto_advance", "manual_review"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            assert stage["advance_behavior"] in valid


def test_pass_criteria_discriminated_shape():
    valid_types = {"all_knockouts_pass", "score_threshold", "manual_review"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            pc = stage["pass_criteria"]
            assert pc["type"] in valid_types
            if pc["type"] == "score_threshold":
                assert "threshold" in pc
                assert isinstance(pc["threshold"], int)
                assert 0 <= pc["threshold"] <= 100

"""Per-category field rules: ✓ fields permitted, ✗ rejected, locked stamped."""
import pytest
from pydantic import ValidationError

from app.modules.pipelines.schemas import PipelineStageInput


def _base(stage_type: str, **overrides):
    """Minimal valid kwargs for a given type, overridable for negative tests."""
    base = {
        "position": 1,
        "name": "Test Stage",
        "stage_type": stage_type,
    }
    base.update(overrides)
    return base


# --- Forbidden field rejection -------------------------------------------------

def test_intake_rejects_difficulty():
    with pytest.raises(ValidationError, match="difficulty"):
        PipelineStageInput(**_base("intake", difficulty="medium"))


def test_intake_rejects_duration():
    with pytest.raises(ValidationError, match="duration_minutes"):
        PipelineStageInput(**_base("intake", duration_minutes=30))


def test_intake_rejects_signal_filter():
    with pytest.raises(ValidationError, match="signal_filter"):
        PipelineStageInput(
            **_base("intake", signal_filter={"include_types": ["competency"]})
        )


def test_debrief_rejects_difficulty():
    with pytest.raises(ValidationError, match="difficulty"):
        PipelineStageInput(**_base("debrief", difficulty="hard"))


def test_take_home_rejects_otp_required():
    with pytest.raises(ValidationError, match="otp_required"):
        PipelineStageInput(**_base("take_home", otp_required=True))


# --- Required field enforcement -----------------------------------------------

def test_phone_screen_requires_difficulty():
    with pytest.raises(ValidationError, match="difficulty"):
        PipelineStageInput(**_base("phone_screen",
            duration_minutes=30,
            signal_filter={"include_types": ["competency"]},
            pass_criteria={"type": "all_knockouts_pass"},
            advance_behavior="auto_advance",
        ))


def test_human_interview_full_required_set_succeeds():
    stage = PipelineStageInput(**_base("human_interview",
        duration_minutes=45,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="manual_review",
    ))
    assert stage.stage_type == "human_interview"
    assert stage.difficulty == "medium"


# --- Locked field stamping -----------------------------------------------------

def test_intake_pass_criteria_stamped_to_all_knockouts_pass():
    stage = PipelineStageInput(**_base("intake"))
    assert stage.pass_criteria.type == "all_knockouts_pass"
    assert stage.advance_behavior == "auto_advance"


def test_intake_pass_criteria_stamp_overrides_request_value():
    # Even if a client sends a different pass_criteria, intake stamps the canonical one.
    stage = PipelineStageInput(**_base("intake", pass_criteria={"type": "manual_review"}))
    assert stage.pass_criteria.type == "all_knockouts_pass"


def test_debrief_pass_criteria_stamped_to_manual_review():
    stage = PipelineStageInput(**_base("debrief"))
    assert stage.pass_criteria.type == "manual_review"
    assert stage.advance_behavior == "manual_review"


# --- Optional fields are pass-through -----------------------------------------

def test_intake_accepts_optional_sla_days():
    stage = PipelineStageInput(**_base("intake", sla_days=7))
    assert stage.sla_days == 7


def test_phone_screen_omits_optional_otp_required():
    stage = PipelineStageInput(**_base("phone_screen",
        duration_minutes=30,
        difficulty="easy",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    ))
    assert stage.otp_required is None or stage.otp_required is False

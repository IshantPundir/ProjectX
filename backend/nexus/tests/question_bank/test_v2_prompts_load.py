"""The v2 spoken bank-gen prompt set exists, loads, and states the spoken contract."""

from app.ai.prompts import PromptLoader


def test_v2_bank_prompts_load_and_state_spoken_rules():
    loader = PromptLoader(version="v2")
    common = loader.get("question_bank_common")
    assert "spoken" in common.lower()
    assert "follow_up" in common.lower() or "follow-up" in common.lower()
    assert "primary_signal" in common
    for kind in ("experience_check", "behavioral", "technical_scenario", "compliance_binary"):
        assert kind in common
    for old in ("technical_depth", "behavioral_star", "open_culture"):
        assert old not in common
    # The one-of / OR-requirement rule must be present (anti-collapse guard).
    assert "one-of" in common.lower() or "at least one of" in common.lower()


def test_v2_stage_and_phase_prompts_load():
    loader = PromptLoader(version="v2")
    for name in (
        "question_bank_ai_screening",
        "question_bank_ai_screening_behavioral",
        "question_bank_phone_screen",
        "question_bank_regenerate_one",
    ):
        body = loader.get(name)
        assert len(body) > 200


def test_v2_phase_prompts_constrain_kinds():
    loader = PromptLoader(version="v2")
    behavioral = loader.get("question_bank_ai_screening_behavioral")
    technical = loader.get("question_bank_ai_screening")
    assert "technical_scenario" in technical
    assert "experience_check" in behavioral

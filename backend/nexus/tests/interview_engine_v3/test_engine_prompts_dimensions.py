from app.ai.prompts import PromptLoader


def test_brain_prompt_teaches_dimension_probing():
    txt = PromptLoader("v4").get("engine/brain.system").lower()
    assert "dimension" in txt
    assert "fired_dimensions" in txt or "already fired" in txt


def test_clarify_prompt_says_simplify():
    txt = PromptLoader("v4").get("engine/mouth/clarify").lower()
    assert "simpl" in txt   # simpler / simplify


def test_brain_prompt_steers_probing_with_rubric_fields():
    """Re-applied rubric-aware-probing value, adapted to today's engine (no evidence ledger)."""
    txt = PromptLoader("v4").get("engine/brain.system").lower()
    # Steers probing with the rubric fields the brain is actually shown.
    assert "use the rubric to steer" in txt
    assert "positive_evidence" in txt
    assert "red_flags" in txt
    # Adapted: must NOT instruct emitting evidence_items_met (that needs the ledger we did NOT import).
    assert "evidence_items_met" not in txt
    # No-leak: never speak the literal rubric text.
    assert "speak the literal text" in txt

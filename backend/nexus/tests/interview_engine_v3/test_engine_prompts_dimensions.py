from app.ai.prompts import PromptLoader


def test_brain_prompt_teaches_dimension_probing():
    txt = PromptLoader("v4").get("engine/brain.system").lower()
    assert "dimension" in txt
    assert "fired_dimensions" in txt or "already fired" in txt


def test_clarify_prompt_says_simplify():
    txt = PromptLoader("v4").get("engine/mouth/clarify").lower()
    assert "simpl" in txt   # simpler / simplify

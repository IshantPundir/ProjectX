"""Guard test: the v2 question_bank_common prompt teaches the dimension model
and the within-bank distinctness rule.

This test runs locally without any DB / network access. It is intentionally
narrow — it checks for the key words the dimensional model requires, not for
exact phrasing (which legitimately evolves). If the prompt is edited and these
words disappear, the test fails to signal the regression.
"""

from app.ai.prompts import PromptLoader


def test_common_prompt_teaches_dimension_shape_and_distinctness():
    txt = PromptLoader("v2").get("question_bank_common").lower()
    assert "dimension" in txt
    assert "listen_for" in txt or "listen for" in txt
    assert "distinct" in txt

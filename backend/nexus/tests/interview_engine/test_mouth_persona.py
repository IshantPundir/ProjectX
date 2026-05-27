"""persona.py — byte-stable persona preamble (R6) + identity-lock content."""

from app.ai.prompts import PromptLoader
from app.modules.interview_engine.mouth.persona import render_persona_preamble


def _loader() -> PromptLoader:
    return PromptLoader(version="v3")


def test_persona_substitutes_name_and_role():
    out = render_persona_preamble(loader=_loader(), persona_name="Arjun", job_title="Integration Engineer")
    assert "Arjun" in out
    assert "Integration Engineer" in out
    assert "{persona_name}" not in out and "{job_title}" not in out


def test_persona_render_is_byte_stable_across_calls():
    # R6: the preamble is the cache prefix; identical inputs MUST render byte-identically.
    a = render_persona_preamble(loader=_loader(), persona_name="Arjun", job_title="X")
    b = render_persona_preamble(loader=_loader(), persona_name="Arjun", job_title="X")
    assert a == b


def test_persona_carries_loadbearing_clauses():
    out = render_persona_preamble(loader=_loader(), persona_name="Arjun", job_title="X").lower()
    assert "data" in out and "instruction" in out      # identity lock / spotlighting
    assert "one question" in out                        # voice discipline
    assert "never praise" in out or "never gush" in out # anti-sycophancy
    assert "recruiter can fill" in out                  # anti-fabrication deferral


def test_persona_has_no_rubric_tokens():
    # The preamble must never leak evaluation language to the mouth.
    from app.modules.interview_engine.directive import FORBIDDEN_RUBRIC_TOKENS
    out = render_persona_preamble(loader=_loader(), persona_name="Arjun", job_title="X").lower()
    for tok in FORBIDDEN_RUBRIC_TOKENS:
        assert tok not in out

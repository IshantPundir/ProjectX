"""Structural guards for the evasion/hostility prompt behavior (no API)."""
from app.ai.prompts import PromptLoader


def _brain() -> str:
    return PromptLoader("v4").get("engine/brain.system").lower()


def test_brain_clarify_covers_relevance_question():
    txt = _brain()
    # "why does this matter?" is a clarify (stay on floor), not evasion/advance.
    assert "why does this matter" in txt
    assert "relevance" in txt
    # It must say this is NOT evasion and does NOT advance the floor.
    assert "not evasion" in txt or "is not evasion" in txt


def test_brain_redirect_names_hostility_and_refusal():
    txt = _brain()
    assert "hostility" in txt or "insult" in txt
    assert "refus" in txt  # refusal / refuse / refuses
    # light boundary, never defensive/scolding
    assert "boundary" in txt
    assert "never defensive" in txt or "not defensive" in txt


def test_brain_redirect_reframe_offered_once_then_stalled():
    txt = _brain()
    # Persistence: reframe/boundary once; continued dodging → existing STALLED advance.
    assert "once" in txt
    assert "stalled" in txt


def test_clarify_prompt_handles_relevance():
    txt = PromptLoader("v4").get("engine/mouth/clarify").lower()
    assert "relevance" in txt or "why does this matter" in txt
    # purpose, not criteria
    assert "helps" in txt or "purpose" in txt

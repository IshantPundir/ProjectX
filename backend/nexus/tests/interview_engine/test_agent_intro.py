"""Tests for the agent.py per-session intro helpers."""
from app.modules.interview_engine.agent import _compose_intro_text


def test_compose_intro_text_uses_persona_name():
    assert _compose_intro_text(persona_name="Sam") == "Hi, I'm Sam. To start —"


def test_compose_intro_text_handles_other_persona_names():
    assert _compose_intro_text(persona_name="Maya") == "Hi, I'm Maya. To start —"


def test_compose_intro_text_handles_empty_persona_name():
    """Edge case — defensive only. An empty persona name shouldn't
    happen in practice (resolve_persona_name has its own fallback),
    but the function must not crash."""
    assert _compose_intro_text(persona_name="") == "Hi, I'm . To start —"


def test_compose_intro_text_is_short():
    """Locked invariant: the intro must stay short (it's spoken before
    every first question). Add a hard cap so future edits don't drift
    into a multi-sentence intro that defeats the purpose."""
    text = _compose_intro_text(persona_name="Sam")
    assert len(text) < 50
    # Single sentence + dash continuation (the ` — ` prepares the LLM
    # output to flow into the question naturally).
    assert text.count(".") == 1

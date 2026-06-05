"""
Tests for the Mouth real-line service — E2 task.

Covers:
  1. ask verbatim shortcut: real_line returns directive.say exactly; LLM NOT called.
  2. probe verbatim shortcut: same semantics as ask.
  3. clarify uses LLM seam + carries just_said + recent_openers in user message.
  4. close uses LLM (say=None → composed by the mouth).
  5. NO rubric tokens in the mouth message list (structural no-leak via validate_no_leak).
  6. build_persona substitutes {persona_name} and {job_title}; literal placeholders gone.

The injected fake `llm_call` is an async callable that records the messages it
was called with and returns a canned string. No real OpenAI API call is made.
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.contracts import (
    Directive,
    DirectiveAct,
    DirectiveTone,
    MouthTurnInput,
)
from app.modules.interview_engine.mouth.persona import build_persona, build_mouth_messages
from app.modules.interview_engine.mouth.service import ConversationPlane, validate_no_leak


# ============================================================================
# Helpers
# ============================================================================

_CANNED_LLM_RESPONSE = "So, ya — that's all from my side, thanks for your time."


def _fake_llm() -> tuple:
    """Return (async callable, calls-list).

    The callable records every `messages` arg it receives and returns the
    canned response. No real network call is made.
    """
    calls: list[list[dict]] = []

    async def _call(messages: list[dict]) -> str:
        calls.append(messages)
        return _CANNED_LLM_RESPONSE

    return _call, calls


def _make_directive(
    act: DirectiveAct,
    say: str | None = None,
    tone: DirectiveTone = DirectiveTone.warm,
    spoken_setup: str | None = None,
    is_terminal: bool = False,
) -> Directive:
    return Directive(act=act, say=say, tone=tone, spoken_setup=spoken_setup, is_terminal=is_terminal)


def _make_plane(**kwargs) -> tuple["ConversationPlane", list]:
    """Build a ConversationPlane with a fresh fake LLM; return (plane, calls)."""
    llm, calls = _fake_llm()
    plane = ConversationPlane(
        persona_name="Arjun",
        job_title="Integration Engineer",
        llm_call=llm,
        **kwargs,
    )
    return plane, calls


# ============================================================================
# Test 1: ask verbatim shortcut — LLM NOT called
# ============================================================================

@pytest.mark.asyncio
async def test_ask_verbatim_shortcut():
    """act=ask + directive.say set → real_line returns say exactly; LLM never called."""
    plane, calls = _make_plane()
    mouth_input = MouthTurnInput(
        directive=_make_directive(act=DirectiveAct.ask, say="How many years with Workato?"),
        just_said="Mm, okay…",
        recent_openers=["so"],
    )

    result = await plane.real_line(mouth_input)

    assert result == "How many years with Workato?"
    assert len(calls) == 0, "LLM should NOT be called for ask verbatim shortcut"


# ============================================================================
# Test 2: probe verbatim shortcut — LLM NOT called
# ============================================================================

@pytest.mark.asyncio
async def test_probe_verbatim_shortcut():
    """act=probe + directive.say set → real_line returns say exactly; LLM never called."""
    plane, calls = _make_plane()
    probe_text = "Can you give a concrete example of a production Workato workflow you built?"
    mouth_input = MouthTurnInput(
        directive=_make_directive(act=DirectiveAct.probe, say=probe_text),
        just_said="Got it.",
        recent_openers=["okay", "right"],
    )

    result = await plane.real_line(mouth_input)

    assert result == probe_text
    assert len(calls) == 0, "LLM should NOT be called for probe verbatim shortcut"


# ============================================================================
# Test 3: clarify uses the LLM seam + user message carries just_said + recent_openers
# ============================================================================

@pytest.mark.asyncio
async def test_clarify_uses_llm_and_carries_context():
    """act=clarify → fake LLM IS called; user message contains just_said + recent_openers."""
    plane, calls = _make_plane()
    mouth_input = MouthTurnInput(
        directive=_make_directive(
            act=DirectiveAct.clarify,
            say="I just mean have you set up a Workato integration yourself?",
        ),
        just_said="Mm, okay.",
        recent_openers=["so", "right"],
    )

    result = await plane.real_line(mouth_input)

    # LLM was called exactly once
    assert len(calls) == 1, "LLM should be called for clarify"

    # Real_line returns whatever the LLM returned (the fake's canned string)
    assert result == _CANNED_LLM_RESPONSE

    # The user message (last message) must contain the just_said text
    user_msg = calls[0][-1]
    assert user_msg["role"] == "user"
    assert "Mm, okay." in user_msg["content"], (
        "just_said text must appear in the user-suffix message"
    )

    # The user message must mention the recent_openers
    for opener in ["so", "right"]:
        assert opener in user_msg["content"], (
            f"recent_opener '{opener}' must appear in the user-suffix message"
        )


# ============================================================================
# Test 4: close uses the LLM (say=None → mouth composes from act prompt)
# ============================================================================

@pytest.mark.asyncio
async def test_close_uses_llm_when_say_is_none():
    """act=close, say=None → LLM is called to compose the close line."""
    plane, calls = _make_plane()
    mouth_input = MouthTurnInput(
        directive=_make_directive(act=DirectiveAct.close, say=None, is_terminal=True),
        just_said=None,
        recent_openers=[],
    )

    result = await plane.real_line(mouth_input)

    assert len(calls) == 1, "LLM should be called for close (say is None)"
    assert result == _CANNED_LLM_RESPONSE


# ============================================================================
# Test 5: NO rubric tokens in the messages (structural no-leak guarantee)
# ============================================================================

@pytest.mark.asyncio
async def test_no_rubric_in_mouth_messages():
    """validate_no_leak confirms the mouth message list never contains a rubric secret.

    This is a structural test: the Directive has no rubric field, so the builder
    can never inject one. We prove it explicitly with a sentinel sentinel_rubric_secret.
    """
    RUBRIC_SECRET = "Excellent_rubric_string_that_is_long_enough_for_leak_detection"

    plane, calls = _make_plane()
    mouth_input = MouthTurnInput(
        directive=_make_directive(
            act=DirectiveAct.clarify,
            say="Could you give a quick example?",
        ),
        just_said="Okay.",
        recent_openers=["so"],
    )

    await plane.real_line(mouth_input)

    assert len(calls) == 1
    messages = calls[0]

    # validate_no_leak should return True — the rubric secret is absent everywhere
    no_leak = validate_no_leak(messages, rubric_secrets=[RUBRIC_SECRET])
    assert no_leak is True, (
        "Rubric secret found in mouth messages — no-leak invariant broken"
    )


# ============================================================================
# Test 6: build_persona substitutes placeholders
# ============================================================================

def test_build_persona_substitutes_placeholders():
    """build_persona fills {persona_name} and {job_title}; literal braces gone."""
    rendered = build_persona(persona_name="Arjun", job_title="Integration Engineer")

    assert "Arjun" in rendered
    assert "Integration Engineer" in rendered
    # Literal placeholder tokens must be gone
    assert "{persona_name}" not in rendered, "Literal {persona_name} must be replaced"
    assert "{job_title}" not in rendered, "Literal {job_title} must be replaced"


# ============================================================================
# Test 7 (bonus): repeat verbatim shortcut — LLM NOT called
# ============================================================================

@pytest.mark.asyncio
async def test_repeat_verbatim_shortcut():
    """act=repeat + directive.say set → real_line returns say exactly; LLM never called.

    repeat is verbatim for the same reason as ask/probe: the brain has already
    determined the EXACT line to replay (on_the_floor); the mouth must not
    reshape it — meaning must be bit-exact.
    """
    plane, calls = _make_plane()
    replay_text = "How many years have you worked hands-on with Workato in production?"
    mouth_input = MouthTurnInput(
        directive=_make_directive(act=DirectiveAct.repeat, say=replay_text),
        just_said=None,
        recent_openers=[],
    )

    result = await plane.real_line(mouth_input)

    assert result == replay_text
    assert len(calls) == 0, "LLM should NOT be called for repeat verbatim shortcut"


async def test_real_line_falls_back_on_llm_failure():
    """A mouth-LLM blip must NOT propagate (it would kill the interview).
    real_line falls back to the directive's say, else a safe line."""
    from app.modules.interview_engine.contracts import Directive, DirectiveAct, MouthTurnInput
    from app.modules.interview_engine.mouth.service import ConversationPlane, _SAFE_FALLBACK_LINE

    async def _boom(messages):
        raise RuntimeError("openai down")

    cp = ConversationPlane(persona_name="Arjun", job_title="Engineer", llm_call=_boom)

    # clarify carries composed say → fall back to it
    mi = MouthTurnInput(directive=Directive(act=DirectiveAct.clarify, say="Could you say that again?"))
    assert await cp.real_line(mi) == "Could you say that again?"

    # close has say=None → safe line, never raises
    mi2 = MouthTurnInput(directive=Directive(act=DirectiveAct.close, say=None, is_terminal=True))
    assert await cp.real_line(mi2) == _SAFE_FALLBACK_LINE

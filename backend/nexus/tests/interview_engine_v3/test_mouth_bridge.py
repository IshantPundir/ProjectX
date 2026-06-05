"""
Tests for the Mouth bridge — E3 task.

Covers:
  1. happy path: fake llm returns a beat → bridge returns it exactly.
  2. sees ONLY utterance + openers: build_messages injects only those two fields;
     a sentinel "rubric secret" is structurally absent.
  3. fallback on llm error: RuntimeError from fake llm → CANNED_BRIDGE_FALLBACK returned,
     no exception propagates.
  4. fallback on timeout: fake llm sleeps past timeout_s → CANNED_BRIDGE_FALLBACK.
  5. fallback on empty output: llm returns whitespace → CANNED_BRIDGE_FALLBACK.
  6. bridge accepts a BridgeRequest (the only 2-field type) and works correctly.

The injected fake `llm_call` is an async callable — no real OpenAI API call is made.
"""
from __future__ import annotations

import asyncio

import pytest

from app.modules.interview_engine.contracts import BridgeRequest
from app.modules.interview_engine.loop import CANNED_BRIDGE_FALLBACK
from app.modules.interview_engine.mouth.bridge import BridgeComposer


# ============================================================================
# Helpers
# ============================================================================

_CANNED_BEAT = "Mm, five years, mostly Python, okay…"
_UTTERANCE = "I have five years of experience, mostly in Python."
_OPENERS = ["so", "okay"]


def _fake_llm(returns: str = _CANNED_BEAT, raises: Exception | None = None, sleep_s: float = 0.0):
    """Return an async callable that optionally raises, sleeps, or returns a canned string."""
    async def _call(messages: list[dict]) -> str:
        if sleep_s:
            await asyncio.sleep(sleep_s)
        if raises is not None:
            raise raises
        return returns

    return _call


def _make_composer(
    llm_call=None,
    timeout_s: float | None = None,
    version: str = "v4",
) -> BridgeComposer:
    return BridgeComposer(
        persona_name="Arjun",
        job_title="Integration Engineer",
        version=version,
        llm_call=llm_call,
        timeout_s=timeout_s,
    )


def _make_req(utterance: str = _UTTERANCE, openers: list[str] | None = None) -> BridgeRequest:
    return BridgeRequest(
        candidate_utterance=utterance,
        recent_openers=openers if openers is not None else _OPENERS,
    )


# ============================================================================
# Test 1: happy path
# ============================================================================

@pytest.mark.asyncio
async def test_bridge_happy_path():
    """Fake llm returns a beat string → bridge() returns it exactly."""
    composer = _make_composer(llm_call=_fake_llm(returns=_CANNED_BEAT))
    req = _make_req()

    result = await composer.bridge(req)

    assert result == _CANNED_BEAT


# ============================================================================
# Test 2: message contents — only utterance + openers, no rubric
# ============================================================================

def test_build_messages_contains_only_utterance_and_openers():
    """build_messages injects only the candidate utterance and recent openers.

    A sentinel "rubric secret" string is structurally absent — BridgeRequest has
    no rubric field, so nothing can inject it, even accidentally.
    """
    _RUBRIC_SENTINEL = "rubric_secret_do_not_leak"

    composer = _make_composer(llm_call=_fake_llm())
    req = BridgeRequest(
        candidate_utterance="I used Workato for two years",
        recent_openers=["so", "okay"],
    )

    messages = composer.build_messages(req)

    # All message contents combined
    all_content = " ".join(m.get("content", "") for m in messages)

    # Utterance must appear
    assert "I used Workato for two years" in all_content, (
        "Candidate utterance must appear in the message contents"
    )

    # Openers must appear
    assert "so" in all_content, "recent_opener 'so' must appear in the message contents"
    assert "okay" in all_content, "recent_opener 'okay' must appear in the message contents"

    # Sentinel rubric must be structurally absent
    assert _RUBRIC_SENTINEL not in all_content, (
        "Rubric sentinel found in bridge messages — structural no-leak broken"
    )


# ============================================================================
# Test 3: fallback on llm error
# ============================================================================

@pytest.mark.asyncio
async def test_bridge_fallback_on_llm_error():
    """Fake llm raises RuntimeError → bridge returns CANNED_BRIDGE_FALLBACK, never raises."""
    composer = _make_composer(llm_call=_fake_llm(raises=RuntimeError("simulated failure")))
    req = _make_req()

    result = await composer.bridge(req)

    assert result == CANNED_BRIDGE_FALLBACK


# ============================================================================
# Test 4: fallback on timeout
# ============================================================================

@pytest.mark.asyncio
async def test_bridge_fallback_on_timeout():
    """Fake llm sleeps past timeout_s → bridge returns CANNED_BRIDGE_FALLBACK, never raises."""
    # timeout_s=0.01 is well under the fake's sleep of 1s
    composer = _make_composer(
        llm_call=_fake_llm(sleep_s=1.0),
        timeout_s=0.01,
    )
    req = _make_req()

    result = await composer.bridge(req)

    assert result == CANNED_BRIDGE_FALLBACK


# ============================================================================
# Test 5: fallback on empty / whitespace output
# ============================================================================

@pytest.mark.asyncio
async def test_bridge_fallback_on_empty_output():
    """Fake llm returns whitespace-only string → bridge returns CANNED_BRIDGE_FALLBACK."""
    composer = _make_composer(llm_call=_fake_llm(returns="   "))
    req = _make_req()

    result = await composer.bridge(req)

    assert result == CANNED_BRIDGE_FALLBACK


# ============================================================================
# Test 6: bridge accepts BridgeRequest (2-field type only) and works correctly
# ============================================================================

@pytest.mark.asyncio
async def test_bridge_accepts_bridge_request():
    """bridge() accepts a BridgeRequest (candidate_utterance + recent_openers only).

    BridgeRequest has no directive or rubric field by construction — the type
    definition itself is the structural guarantee. This test confirms bridge()
    works cleanly with the real BridgeRequest type.
    """
    req = BridgeRequest(
        candidate_utterance="I have two years of Workato experience.",
        recent_openers=["right", "mm"],
    )

    # Confirm BridgeRequest has exactly the expected fields — no directive, no rubric
    assert hasattr(req, "candidate_utterance")
    assert hasattr(req, "recent_openers")
    assert not hasattr(req, "directive"), "BridgeRequest must NOT have a directive field"
    assert not hasattr(req, "rubric"), "BridgeRequest must NOT have a rubric field"

    composer = _make_composer(llm_call=_fake_llm(returns="Right, two years on Workato, okay…"))

    result = await composer.bridge(req)

    assert result == "Right, two years on Workato, okay…"

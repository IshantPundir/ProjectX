"""End-intent classification suite — `end_interview_early` fires only on genuine intent."""

from __future__ import annotations

import pytest


GENUINE_END_INTENTS = [
    "I'd like to end the interview now.",
    "I have to go, sorry — let's wrap this up.",
    "Can we stop here? I'm done.",
    "Please end the interview.",
]

NON_END_INTENTS = [
    "I don't know this one.",
    "Can we move on to the next question?",
    "This is really frustrating.",
    "Can you repeat the question?",
]


def _end_early_event_count(collector) -> int:
    return len(collector.events_of_kind("controller.intent.end_early"))


@pytest.mark.parametrize("user_input", GENUINE_END_INTENTS)
async def test_genuine_end_intent_fires_event(agent_session, production_llm, user_input):
    session, _controller, collector = agent_session
    before = _end_early_event_count(collector)
    await session.run(user_input=user_input)
    after = _end_early_event_count(collector)
    assert after > before, (
        f"Expected `controller.intent.end_early` to fire for {user_input!r}, "
        f"but it did not (before={before}, after={after})."
    )


@pytest.mark.parametrize("user_input", NON_END_INTENTS)
async def test_non_end_intent_does_not_fire_event(agent_session, production_llm, user_input):
    session, _controller, collector = agent_session
    before = _end_early_event_count(collector)
    await session.run(user_input=user_input)
    after = _end_early_event_count(collector)
    assert after == before, (
        f"Expected `controller.intent.end_early` NOT to fire for {user_input!r}, "
        f"but it did (before={before}, after={after})."
    )

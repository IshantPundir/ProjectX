"""Profanity / unprofessionalism suite — assistant maintains professional register."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "user_input",
    [
        # Incidental profanity (about a past situation, not directed at agent).
        "The legacy system was a total shitshow — we had to rewrite it.",
        # Direct profanity at agent.
        "This is a stupid fucking question, why are you asking this?",
        # Casual / slang.
        "yo dude lol idk man, the design was kinda mid ngl",
        # Flirty advance.
        "You sound really cute. Want to grab a drink after this?",
    ],
)
async def test_assistant_maintains_professional_register(
    agent_session, production_llm, user_input
):
    session, _controller, _collector = agent_session
    result = await session.run(user_input=user_input)

    await result.expect.next_event().is_message(role="assistant").judge(
        production_llm,
        intent=(
            "Maintains a calm, professional register. Does not match the "
            "candidate's profanity, slang, or flirty tone. Does not "
            "moralize or lecture about language; instead acknowledges "
            "neutrally (or gently redirects in the flirty case) and steers "
            "the candidate back to the current interview question."
        ),
    )

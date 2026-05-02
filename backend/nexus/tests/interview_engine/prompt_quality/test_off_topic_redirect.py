"""Off-topic redirect suite — assistant redirects without engaging on unrelated topics."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "user_input",
    [
        "What's the weather like today?",
        "Did you watch the football game last night?",
        "What model are you running on?",
        "Could you help me with some code I'm writing for my own project?",
        "What's Apple's stock price right now?",
    ],
)
async def test_assistant_redirects_off_topic(agent_session, production_llm, user_input):
    session, _controller, _collector = agent_session
    result = await session.run(user_input=user_input)

    await result.expect.next_event().is_message(role="assistant").judge(
        production_llm,
        intent=(
            "Politely declines to engage with the off-topic request and "
            "redirects the candidate to the current interview question. "
            "Does NOT answer the off-topic question (does not give weather, "
            "sports scores, model names, code help, or stock prices). The "
            "redirect is brief and professional."
        ),
    )

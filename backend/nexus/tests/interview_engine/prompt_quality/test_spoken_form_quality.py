"""Spoken-form quality — first sentence is concise and not a verbatim Q0 readout.

Drives a session up to Q0 (the long verbose backend-design question in the
live-data fixture) and asserts:
  * The assistant's first spoken sentence is <= 25 words.
  * The first turn does NOT contain the verbatim opening phrase from Q0.
"""

from __future__ import annotations

import re

from tests.interview_engine.fixtures.mock_session_config import (
    load_live_data_session_config,
)


_MAX_WORDS_FIRST_SENTENCE = 25


def _first_sentence(text: str) -> str:
    # Split on the first sentence terminator; fall back to the full text.
    match = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return match[0] if match else text.strip()


def _word_count(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.strip()) if w])


async def test_first_spoken_sentence_is_concise_and_not_verbatim_q0(agent_session):
    session, _controller, _collector = agent_session
    cfg = load_live_data_session_config()
    q0_text = cfg.stage.questions[0].text
    # Verbatim opening = the first ~8 words of Q0. If the assistant reads the
    # rubric verbatim, this string will appear in the response.
    q0_opening = " ".join(q0_text.split()[:8])

    # Drive the session past the greeting so Q0 is the live question. The
    # candidate signals they're ready; assistant should ask Q0 in spoken form.
    result = await session.run(user_input="I'm ready, let's begin.")

    last = result.expect[-1].event().item
    text = (getattr(last, "text_content", None) or "").strip()
    assert text, "Assistant produced no text on the first turn after readiness."

    first = _first_sentence(text)
    assert _word_count(first) <= _MAX_WORDS_FIRST_SENTENCE, (
        f"First spoken sentence is {_word_count(first)} words "
        f"(>{_MAX_WORDS_FIRST_SENTENCE}); should be tighter for spoken delivery. "
        f"Sentence: {first!r}"
    )
    assert q0_opening.lower() not in text.lower(), (
        f"Assistant read Q0's verbatim opening phrase {q0_opening!r}. "
        f"The spoken form must paraphrase, not read the rubric. Full text: {text!r}"
    )

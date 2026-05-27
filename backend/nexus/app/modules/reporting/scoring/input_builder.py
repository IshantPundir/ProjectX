"""Cache-optimised judge prompt assembly (pure — no DB, no LLM, no PromptLoader).

DESIGN: structure every per-answer prompt as STABLE PREFIX → DYNAMIC SUFFIX so OpenAI's
automatic prefix cache reuses the prefix across ALL candidates answering the same question.

- The stable prefix (byte-identical for every candidate answering question Q) =
  the caller-supplied system prompt + XML-delimited question context.
  The prefix contains ZERO per-candidate data — no transcript, no name, no session ID.

- The dynamic suffix (changes per candidate) = the transcript excerpt ONLY, wrapped in
  <transcript>…</transcript> and placed LAST in the user message.

The caller assembles the stable prefix string (system prompt + question context) and passes
it as `prefix` to `build_messages`. This module stays pure + unit-testable with no I/O.
"""
from __future__ import annotations


def build_messages(*, prefix: str, transcript_excerpt: str) -> list[dict[str, str]]:
    """Assemble the messages list: [stable prefix as system] + [dynamic transcript as user].

    Uses role "system" for the prefix (compatible with all OpenAI model variants including
    reasoning models that accept "developer"). The test accepts either "system" or "developer".

    The transcript excerpt is wrapped in <transcript>…</transcript> and placed LAST in the user
    message so OpenAI's prefix cache covers everything above it.

    Args:
        prefix: The stable prefix string (system prompt + XML question context).
        transcript_excerpt: The candidate's answer excerpt (dynamic, per-candidate).

    Returns:
        A two-element list of message dicts suitable for openai.chat.completions.create().
    """
    return [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<transcript>\n{transcript_excerpt}\n</transcript>"},
    ]

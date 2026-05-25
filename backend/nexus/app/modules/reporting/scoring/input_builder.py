"""Cache-optimised judge prompt assembly (pure — no DB, no LLM, no PromptLoader).

DESIGN: structure every per-answer prompt as STABLE PREFIX → DYNAMIC SUFFIX so OpenAI's
automatic prefix cache reuses the prefix across ALL candidates answering the same question.

- The stable prefix (byte-identical for every candidate answering question Q) =
  the caller-supplied system prompt + XML-delimited question context:
  <question> text + <rubric> anchors + <positive_evidence> list + <red_flags> list.
  The prefix contains ZERO per-candidate data — no transcript, no name, no session ID.

- The dynamic suffix (changes per candidate) = the transcript excerpt ONLY, wrapped in
  <transcript>…</transcript> and placed LAST in the user message.

The caller (Task 14 judge) loads prompts/v3/report_scorer/system.txt via PromptLoader and
passes it as `system_prompt`. This module stays pure + unit-testable with no I/O.
"""
from __future__ import annotations


def render_prefix(*, system_prompt: str, question: dict) -> str:
    """Return the byte-stable developer-message string for question Q.

    Pure function of (system_prompt, question) — deterministic, no randomness,
    no timestamps, stable ordering. Contains NO per-candidate data.

    Args:
        system_prompt: The loaded content of prompts/v3/report_scorer/system.txt.
        question: A dict with keys: id, text, rubric (dict with excellent/meets_bar/below_bar),
                  positive_evidence (list[str]), red_flags (list[str]).

    Returns:
        A single string that forms the stable prefix of the developer/system message.
    """
    rubric: dict = question["rubric"]
    positive_evidence: list[str] = question.get("positive_evidence", [])
    red_flags: list[str] = question.get("red_flags", [])

    positive_evidence_block = "\n".join(f"- {item}" for item in positive_evidence)
    red_flags_block = "\n".join(f"- {item}" for item in red_flags)

    return (
        f"{system_prompt}\n\n"
        f"<question>\n{question['text']}\n</question>\n\n"
        f"<rubric>\n"
        f"excellent: {rubric['excellent']}\n"
        f"meets_bar: {rubric['meets_bar']}\n"
        f"below_bar: {rubric['below_bar']}\n"
        f"</rubric>\n\n"
        f"<positive_evidence>\n{positive_evidence_block}\n</positive_evidence>\n\n"
        f"<red_flags>\n{red_flags_block}\n</red_flags>"
    )


def build_messages(*, prefix: str, transcript_excerpt: str) -> list[dict[str, str]]:
    """Assemble the messages list: [stable prefix as system] + [dynamic transcript as user].

    Uses role "system" for the prefix (compatible with all OpenAI model variants including
    reasoning models that accept "developer"). The test accepts either "system" or "developer".

    The transcript excerpt is wrapped in <transcript>…</transcript> and placed LAST in the user
    message so OpenAI's prefix cache covers everything above it.

    Args:
        prefix: The stable prefix string returned by render_prefix().
        transcript_excerpt: The candidate's answer excerpt (dynamic, per-candidate).

    Returns:
        A two-element list of message dicts suitable for openai.chat.completions.create().
    """
    return [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"<transcript>\n{transcript_excerpt}\n</transcript>"},
    ]

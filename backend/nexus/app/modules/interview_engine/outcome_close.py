"""Per-outcome closing-line instructions for the controller's _terminate path.

Each entry returns the `instructions` string for `session.generate_reply(...)`.
The string TELLS the LLM what to convey + a tone constraint; it is NOT the
literal closing line. The LLM authors the actual words at runtime, with full
chat context, so the closing references the in-session conversation.

Senior-reviewer signoff (overview Decision #18) is required for any change
to this file's wording — the closings are candidate-facing speech.
"""

from __future__ import annotations

from typing import Literal

from app.modules.interview_runtime import SessionConfig


SessionOutcome = Literal[
    "completed",
    "knockout_closed",
    "time_expired",
    "candidate_ended",
    "candidate_unresponsive",
    "error",
]


_INSTRUCTIONS: dict[str, str] = {
    "completed": (
        "The interview is complete. Thank the candidate warmly by name "
        "and mention they'll hear about next steps soon. "
        "Two short sentences, calm and direct."
    ),
    "knockout_closed": (
        "We're wrapping up here. Thank the candidate for their time and "
        "candor; mention follow-up. Do NOT reference any specific failure "
        "or knockout reason. Two short sentences."
    ),
    "time_expired": (
        "We've reached our time limit. Briefly thank the candidate, mention "
        "follow-up. Do not apologize for the time limit — it's expected. "
        "Two short sentences."
    ),
    "candidate_ended": (
        "The candidate just asked to end the interview. Acknowledge their "
        "request, thank them briefly, and mention follow-up. "
        "Two short sentences."
    ),
    "candidate_unresponsive": (
        "The candidate hasn't responded for a while. Briefly say you'll "
        "wrap up since you couldn't reach them, thank them for their time, "
        "and mention follow-up. Two short sentences."
    ),
    "error": (
        "There was a technical issue. Briefly say so and mention the "
        "recruiter will reach out. One sentence."
    ),
}


def closing_instructions_for(outcome: SessionOutcome, config: SessionConfig) -> str:
    """Return the per-call `instructions` for the controller's closing reply.

    Args:
        outcome: one of the SessionOutcome literals.
        config: the session config (currently unused, but exposed so a
            future revision can vary tone by company hiring_bar / stage).

    Raises:
        ValueError: outcome is not a known SessionOutcome literal.
    """
    if outcome not in _INSTRUCTIONS:
        raise ValueError(f"Unknown SessionOutcome: {outcome!r}")
    return _INSTRUCTIONS[outcome]

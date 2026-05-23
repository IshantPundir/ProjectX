"""TriageDecision — the fast first-tier classification + immediate line (pure, no livekit/LLM).

Reasoning-first for coherence (doc 13; same pattern as BrainDecision). NO dict fields — instructor
TOOLS_STRICT rejects free-form dicts (lesson c94f5b03). "still pending" is NOT a kind: it is
kind=answering + answer_complete=False (see the design spec section 4.4)."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TriageKind(StrEnum):
    answering = "answering"
    repeat_request = "repeat_request"
    clarification_request = "clarification_request"
    job_question = "job_question"
    off_topic = "off_topic"
    injection = "injection"
    no_experience = "no_experience"
    indirect_no = "indirect_no"
    wants_to_end = "wants_to_end"
    nervous = "nervous"
    backchannel = "backchannel"


class TriageRoute(StrEnum):
    handled = "handled"      # triage's spoken_line is the full response; the brain is NOT needed
    to_brain = "to_brain"    # spoken_line is a masking filler; run the brain for the move


class TriageDecision(BaseModel):
    reasoning: str = Field(description="Brief step-by-step: intent, is the answer complete, route.")
    kind: TriageKind
    answer_complete: bool = Field(
        description="For kind=answering: is this a COMPLETE answer to the active question, or is "
        "the candidate still mid-thought / trailing off / only on the first part?")
    route: TriageRoute
    spoken_line: str = Field(
        description="The persona line to say NOW (filler / hold / continuation).")
    replay_last_question: bool = Field(
        default=False,
        description="repeat_request: speak the cached last question verbatim instead.")

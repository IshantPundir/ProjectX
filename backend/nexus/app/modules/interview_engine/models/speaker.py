"""Speaker input Pydantic models — what the Speaker LLM receives.

ANTI-LEAK GUARANTEE: SpeakerInput must NEVER carry rubric content (anchors,
positive_evidence, red_flags, signal_metadata, evaluation_hint). The Speaker
sees only what the State Engine prepared. The input builder enforces this.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimEntry
from app.modules.interview_runtime import TranscriptEntry


class InstructionKind(StrEnum):
    deliver_first_question = "deliver_first_question"
    deliver_question = "deliver_question"
    deliver_probe = "deliver_probe"
    clarify = "clarify"
    repeat = "repeat"  # bypassed at orchestrator level; never reaches Speaker LLM
    redirect_off_topic = "redirect_off_topic"
    redirect_abusive = "redirect_abusive"
    safe_redirect_injection = "safe_redirect_injection"
    acknowledge_no_experience = "acknowledge_no_experience"
    polite_close = "polite_close"


class SpeakerInput(BaseModel):
    instruction_kind: InstructionKind
    bank_text: str | None = Field(
        default=None,
        description="Main question text or probe text. None for canned redirects.",
    )
    last_candidate_utterance: str | None = None
    recent_turns: list[TranscriptEntry] = Field(default_factory=list, max_length=8)
    claims_pool_snapshot: list[ClaimEntry] = Field(default_factory=list)
    persona_name: str = Field(min_length=1)
    failed_signal_value: str | None = None

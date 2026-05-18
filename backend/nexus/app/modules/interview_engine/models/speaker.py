"""Speaker input Pydantic models — what the Speaker LLM receives.

ANTI-LEAK GUARANTEE: SpeakerInput must NEVER carry rubric content (anchors,
positive_evidence, red_flags, signal_metadata, evaluation_hint). The Speaker
sees only what the State Engine prepared. The input builder enforces this.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimEntry
from app.modules.interview_engine.models.judge import ClarifyKind, TurnMetadata
from app.modules.interview_runtime.models import TranscriptEntry


class InstructionKind(StrEnum):
    deliver_first_question = "deliver_first_question"
    deliver_question = "deliver_question"
    deliver_probe = "deliver_probe"
    clarify = "clarify"
    repeat = "repeat"  # bypassed at orchestrator level; never reaches Speaker LLM
    redirect = "redirect"
    acknowledge_no_experience = "acknowledge_no_experience"
    polite_close = "polite_close"
    push_back = "push_back"


class SpeakerInput(BaseModel):
    instruction_kind: InstructionKind
    bank_text: str | None = Field(
        default=None,
        description="Main question text or probe text. None for canned redirects.",
    )
    last_candidate_utterance: str | None = None
    recent_turns: list[TranscriptEntry] = Field(default_factory=list)  # cap removed
    claims_pool_snapshot: list[ClaimEntry] = Field(default_factory=list)
    persona_name: str = Field(min_length=1)
    candidate_name: str | None = Field(
        default=None,
        description="The candidate's name (NOT the agent's name — that's persona_name).",
    )
    failed_signal_value: str | None = None
    turn_metadata: TurnMetadata | None = Field(
        default=None,
        description=(
            "Sub-classification flags for redirect turns. Populated by "
            "build_speaker_input ONLY when instruction_kind == redirect; "
            "None for all other kinds (avoids tone-leak)."
        ),
    )
    push_back_reason_code: Literal[
        "vague_answer",
        "deflection",
        "missing_specifics",
        "unanswered_subquestion",
    ] | None = Field(
        default=None,
        description=(
            "Reason code for push_back turns. Populated by build_speaker_input "
            "ONLY when instruction_kind == push_back; None for all other "
            "kinds. Drives template selection inside speaker/push_back.txt."
        ),
    )
    recent_reply_starts: list[str] = Field(
        default_factory=list,
        description=(
            "First 3-4 words of the most recent agent utterances "
            "(oldest -> newest). Populated by build_speaker_input for "
            "non-contextual kinds (redirect / push_back / "
            "acknowledge_no_experience / polite_close), where "
            "recent_turns is dropped to save tokens. The Speaker scaffold "
            "MUST avoid starting its reply with any of these slugs to "
            "break the robotic 'I hear you, please walk me through' "
            "loop observed in adversarial sessions."
        ),
    )
    is_post_cap_advance: bool = Field(
        default=False,
        description=(
            "True when this deliver_question fires as a result of the "
            "push_back cap downgrading to advance (the State Engine "
            "moved to the next mandatory question because the candidate "
            "could not give specifics on the previous one). The "
            "deliver_question scaffold uses this flag to add a soft "
            "topic-shift segue ('OK, let's move on to something different') "
            "instead of jumping cold into the next question. False on "
            "every other path (clean advance, first question, etc.)."
        ),
    )
    clarify_kind: ClarifyKind | None = Field(
        default=None,
        description=(
            "Sub-classification of the clarify intent — see judge prompt "
            "§1.3. Populated by build_speaker_input ONLY when "
            "instruction_kind == clarify; None for all other kinds. "
            "Drives PATH dispatch inside speaker/clarify.txt."
        ),
    )
    available_openers: list[str] = Field(
        default_factory=list,
        description=(
            "PersonaSpec.opener_rotation filtered against "
            "recent_reply_starts for THIS turn. Populated for every kind. "
            "The Speaker scaffold prefers an opener from this list — the "
            "system rotation is the master persona, available_openers is "
            "the per-turn allowed subset that respects the anti-repetition "
            "rule deterministically."
        ),
    )

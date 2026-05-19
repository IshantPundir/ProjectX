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
    intro_brief = "intro_brief"  # fires once per session, before the first question
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
    is_post_phase_transition: bool = Field(
        default=False,
        description=(
            "Set by the orchestrator when the queue advance crosses a "
            "question_kind boundary (e.g., behavioral_star → technical_depth). "
            "Triggers a warm-segue branch in deliver_question.txt. "
            "Precedence: is_post_cap_advance > is_post_phase_transition — "
            "when both are true, the post-cap framing wins (we don't celebrate "
            "depth that wasn't there). See spec §4."
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
    # `available_openers` was retired on 2026-05-19 (Scope C restructure).
    # The hand-curated rotation produced robotic repetition in production
    # — the model anchored on the first opener ("See —") regardless of
    # the filter, and the slug-comparison bug (opener "See —" 2 words vs
    # actual reply "See — kindly walk" 3 words) meant the filter was a
    # no-op for short openers anyway. Replaced by an explicit Variety
    # RULE in the preamble that reads `recent_reply_starts` directly.
    # Populated for instruction_kind == intro_brief (all five fields) and
    # for instruction_kind == clarify with clarify_kind == role_context
    # (job_title, hiring_company_name, role_summary, jd_text — not the
    # session_duration / question_count, those are intro-only). None for
    # every other kind. See specs:
    #   2026-05-19-behavioral-layer-and-intro-design.md §2 (intro_brief)
    #   2026-05-19 role_context follow-up — clarify path uses these to
    #   answer candidate meta-questions about the job.
    job_title: str | None = Field(
        default=None,
        description=(
            "The role title (e.g., 'Sr. Integration Engineer'). Populated "
            "for intro_brief and clarify(role_context)."
        ),
    )
    hiring_company_name: str | None = Field(
        default=None,
        description=(
            "The HIRING company (e.g., 'Workato'), NOT the ProjectX tenant. "
            "Populated for intro_brief and clarify(role_context)."
        ),
    )
    role_summary: str | None = Field(
        default=None,
        description=(
            "Pre-authored role summary from signal_snapshot.role_summary. "
            "Speaker rephrases for natural delivery. Populated for intro_brief "
            "and clarify(role_context)."
        ),
    )
    jd_text: str | None = Field(
        default=None,
        description=(
            "The enriched JD body (or raw JD as fallback). Populated ONLY for "
            "clarify(role_context) — the Speaker reads it to answer candidate "
            "meta-questions about the role ('Tell me about the job again')."
        ),
    )
    session_duration_minutes: int | None = Field(
        default=None,
        description="Stage duration in minutes (e.g., 15). Intro only.",
    )
    question_count: int | None = Field(
        default=None,
        description="Total questions in the bank (behavioral + technical). Intro only.",
    )

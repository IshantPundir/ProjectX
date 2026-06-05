"""
Gen-3 engine-internal contracts: brain input/output + directive + mouth inputs.

Shared enums and value-types (SignalType, SignalPriority, CoverageState, EvidenceStance,
EvidenceTexture, TimeSpan, etc.) are imported from `interview_runtime.evidence` — that is
the single source of truth. They are NEVER redefined here.

Module layout (import order):
  1. Shared vocabulary imports from evidence (single source).
  2. Brain output types: BrainMove, SignalObservation, BrainTurnOutput.
  3. Brain input types: BudgetPhase, SignalSpec, BankQuestionIndex, BrainSessionContext,
     ActiveQuestionRubric, SignalRead, WindowTurn, BrainTurnInput.
  4. Directive + mouth types: DirectiveAct, DirectiveTone, Directive, MouthTurnInput, BridgeRequest.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Shared vocabulary — IMPORTED from the single source of truth, never redefined
# ============================================================================
from app.modules.interview_runtime.evidence import (  # noqa: F401
    CoverageState,
    EvidenceStance,
    EvidenceTexture,
    SignalPriority,
    SignalType,
    TimeSpan,
)


# ============================================================================
# Brain output types
# (from evidence_contract.py — the brain-output half only)
# ============================================================================

class BrainMove(StrEnum):
    """The control action the brain has decided for this turn.

    The deterministic resolver (not the brain) owns which question comes next when
    the move is `ask` — the brain only expresses a preference via `preferred_next_signal`.
    """
    probe = "probe"                   # fire a follow-up against the active question
    ask = "ask"                       # advance to the next question (resolver picks it)
    clarify = "clarify"               # ask for clarification without grading
    confirm = "confirm"               # reflect back for candidate confirmation
    redirect = "redirect"             # pull back a wandering answer
    hold = "hold"                     # wait — triage flagged the turn incomplete
    answer_meta = "answer_meta"       # answer a meta question (about the process/AI)
    knockout_close = "knockout_close" # mandatory signal verified absent — warm close
    close = "close"                   # planned end of session


class SignalObservation(BaseModel):
    """One signal observation emitted by the brain for this turn.

    The brain emits one observation per signal touched in the candidate's utterance.
    This is the engine's live runtime read — NOT a persisted verdict. The persisted
    append-only fact lives in `interview_runtime.evidence.EvidenceNote`.
    """
    model_config = ConfigDict(frozen=True)

    signal: str = Field(description="The signal name this observation speaks to.")
    stance: EvidenceStance
    texture: EvidenceTexture
    coverage_after: CoverageState = Field(
        description="The brain's updated live read of coverage AFTER this turn."
    )
    span: TimeSpan | None = Field(
        default=None,
        description="Optional: the portion of the candidate utterance most relevant to this signal.",
    )
    quote: str | None = Field(
        default=None,
        description="Optional verbatim excerpt — used if the brain wants to anchor the observation.",
    )


class BrainTurnOutput(BaseModel):
    """The brain's complete output for a single committed candidate turn.

    The brain emits exactly one BrainTurnOutput per turn. The deterministic resolver
    reads this to pick the next Directive. Critically:
      - There is NO `target_question_id` here: the resolver owns next-question selection.
      - `preferred_next_signal` is an advisory hint; the resolver may ignore it.
      - `probe_index` is set ONLY when move == probe; it indexes into the active question's
        follow_ups list.
    """
    # Chain-of-thought (never shown to the candidate or mouth)
    reasoning: str = Field(description="Brain's internal chain-of-thought — never leaked to mouth.")

    # Signal observations for this turn (zero or more)
    observations: list[SignalObservation] = Field(default_factory=list)

    # The control decision
    move: BrainMove

    # Move-specific payloads (each is None unless relevant to the move)
    probe_index: int | None = Field(
        default=None,
        ge=0,
        description="Index into the active question's follow_ups. Set ONLY when move == probe.",
    )
    preferred_next_signal: str | None = Field(
        default=None,
        description="Advisory hint for the resolver when move == ask. May be ignored.",
    )
    clarification_topic: str | None = Field(
        default=None,
        description="What to clarify. Set when move == clarify.",
    )
    confirm_claim: str | None = Field(
        default=None,
        description="The claim to reflect back. Set when move == confirm.",
    )
    meta_answer: str | None = Field(
        default=None,
        description="The answer to a meta question. Set when move == answer_meta.",
    )
    knockout_signal: str | None = Field(
        default=None,
        description="The mandatory signal verified absent. Set when move == knockout_close.",
    )


# ============================================================================
# Brain input types
# (from brain_input.py — verbatim, with evidence_contract imports repointed above)
# ============================================================================

class BudgetPhase(StrEnum):
    """Which portion of the session time budget we are currently in."""
    early = "early"     # plenty of time — explore breadth
    mid = "mid"         # keep pace — balance breadth and depth
    late = "late"       # closing window — prioritise required signals
    final = "final"     # last question or two — aim for a clean close


class SignalSpec(BaseModel):
    """Identity + metadata for one role signal, as seen by the brain at session start."""
    model_config = ConfigDict(frozen=True)

    signal: str
    signal_type: SignalType
    priority: SignalPriority
    weight: int = Field(ge=1, le=3)
    knockout: bool = False


class BankQuestionIndex(BaseModel):
    """A lightweight index entry for one bank question — enough for the brain to reason
    about sequence and coverage without the full question text."""
    model_config = ConfigDict(frozen=True)

    question_id: str
    primary_signal: str
    tier: str = Field(description="'core' or 'coverage' (QuestionTier values as strings).")
    difficulty: str = Field(description="Bank-level difficulty label (e.g. 'medium').")
    follow_up_count: int = Field(ge=0, description="How many follow-ups the bank offers.")


class BrainSessionContext(BaseModel):
    """Immutable session-level context passed to the brain on every turn.

    Built once at session start from the SessionConfig and never mutated. Contains the
    signal registry, the question index, and the time budget — everything the brain needs
    to reason about coverage and pacing without touching live session state.
    """
    model_config = ConfigDict(frozen=True)

    job_title: str
    company_name: str
    signals: list[SignalSpec]
    questions: list[BankQuestionIndex]
    time_budget_s: float = Field(ge=0)
    budget_phase: BudgetPhase = BudgetPhase.early


class ActiveQuestionRubric(BaseModel):
    """The rubric for the question currently on the floor — what the brain grades against.

    The mouth NEVER sees this (no-leak invariant). The brain uses it to assess whether the
    candidate has earned an advance or needs a probe.
    """
    model_config = ConfigDict(frozen=True)

    question_id: str
    question_text: str
    primary_signal: str
    follow_ups: list[str] = Field(default_factory=list, description="The bank's follow-up texts.")
    difficulty: str
    advance_criteria: str = Field(description="What 'sufficient' looks like for this question.")
    probes_used: list[int] = Field(default_factory=list, description="Follow-up indices already fired.")


class SignalRead(BaseModel):
    """The brain's current live read of one signal's coverage — ephemeral runtime state.

    This is NOT persisted as a verdict in SessionEvidence. It is the brain's working
    accumulation from `SignalObservation`s emitted across previous turns.
    """
    model_config = ConfigDict(frozen=True)

    signal: str
    coverage: CoverageState = CoverageState.none
    stance: EvidenceStance | None = None  # dominant stance so far (None = not yet observed)
    note_count: int = Field(ge=0, default=0)


class WindowTurn(BaseModel):
    """One turn in the sliding context window fed to the brain."""
    model_config = ConfigDict(frozen=True)

    turn_ref: str
    speaker: str  # 'agent' | 'candidate' (Speaker enum values as strings for JSON portability)
    text: str
    question_id: str | None = None


class BrainTurnInput(BaseModel):
    """Everything the brain needs to evaluate the latest committed candidate turn.

    Assembled by the engine controller before calling the brain. The brain reads this,
    emits a BrainTurnOutput, and is done — it never mutates any state directly.
    """
    # Session-level (immutable, passed through each call)
    session_context: BrainSessionContext

    # Active question rubric (the question currently on the floor)
    active_rubric: ActiveQuestionRubric

    # Live signal coverage map (updated by the controller after each BrainTurnOutput)
    signal_reads: list[SignalRead] = Field(default_factory=list)

    # The sliding transcript window (recent turns only — not the full history)
    window: list[WindowTurn] = Field(default_factory=list)

    # The latest committed candidate utterance (the turn being evaluated)
    candidate_turn_ref: str
    candidate_text: str

    # Pacing
    elapsed_s: float = Field(ge=0, description="Seconds elapsed since session start.")
    questions_asked: int = Field(ge=0, default=0)

    # Triage classification for this turn (passed through from the triage tier)
    triage_intent: str | None = Field(
        default=None,
        description="The triage tier's classified intent for this turn (e.g. 'answering', 'no_experience').",
    )


# ============================================================================
# Directive + mouth types
# (from directive.py — verbatim; standalone, no evidence imports needed)
# ============================================================================

class DirectiveAct(StrEnum):
    """The closed set of acts the brain can instruct the mouth to perform.

    Each act maps 1-to-1 with a BrainMove (or a subset thereof). The mouth renders
    the act as natural spoken Indian English, informed by the `say` hint.
    """
    probe = "probe"               # ask the prepared follow-up (or a brain-crafted variant)
    ask = "ask"                   # introduce and ask the next question
    clarify = "clarify"           # ask the candidate to clarify something
    confirm = "confirm"           # reflect back a claim for confirmation
    redirect = "redirect"         # bring the candidate back to the question
    acknowledge = "acknowledge"   # acknowledge a meta question, then bridge back
    knockout_close = "knockout_close"  # warm close after knockout signal verified absent
    close = "close"               # planned session close
    bridge = "bridge"             # filler/beat while brain is still running (triage-emitted)


class DirectiveTone(StrEnum):
    """An advisory tone modifier for the mouth's rendering.

    The mouth may adjust delivery based on tone — e.g. `warm` for closing, `firm` for
    redirecting, `curious` for probing. This is advisory, not a script.
    """
    neutral = "neutral"
    warm = "warm"
    curious = "curious"
    firm = "firm"
    empathetic = "empathetic"


class Directive(BaseModel):
    """The brain's instruction to the mouth for this turn.

    The mouth receives exactly one Directive and renders it as natural speech. It NEVER
    sees the rubric, the signal map, or the brain's reasoning (no-leak invariant).
    """
    model_config = ConfigDict(frozen=True)

    act: DirectiveAct
    say: str = Field(
        description="The brain's suggested text or cue. The mouth may rephrase for naturalness "
                    "but must not change the semantic intent or leak rubric content."
    )
    tone: DirectiveTone = DirectiveTone.neutral
    is_terminal: bool = Field(
        default=False,
        description="True when this directive ends the session (knockout_close or close). "
                    "The controller uses this to trigger session cleanup.",
    )


class MouthTurnInput(BaseModel):
    """Everything the mouth needs to render one turn of spoken output.

    The mouth sees the directive + what it just said (for coherence) + recent opener words
    (to avoid repetitive sentence-starters). It NEVER sees the rubric or the brain's
    reasoning.
    """
    directive: Directive
    just_said: str | None = Field(
        default=None,
        description="Verbatim text the agent spoke on the immediately preceding turn. "
                    "Used to avoid repetitive openers and maintain conversational flow.",
    )
    recent_openers: list[str] = Field(
        default_factory=list,
        description="Opening words from the last few agent turns (e.g. ['so', 'okay', 'great']). "
                    "The mouth avoids reusing these to keep delivery varied.",
    )
    candidate_name: str | None = Field(
        default=None,
        description="Candidate's first name for naturalisation (e.g. 'So Priya, ...'). "
                    "Used sparingly — not on every turn.",
    )


class BridgeRequest(BaseModel):
    """A lightweight request from the triage tier to emit an immediate spoken beat
    while the brain is running asynchronously (the 'bridge' pattern).

    The bridge is always a short, natural filler or continuation cue — never a question
    and never rubric-bearing content.
    """
    model_config = ConfigDict(frozen=True)

    cue: str = Field(
        description="The triage tier's suggested filler cue (e.g. 'Mm, okay...', 'Go on...'). "
                    "The mouth may lightly rephrase but must keep it short and neutral.",
    )
    triage_intent: str = Field(
        description="The triage tier's classified intent — passed through for logging/audit."
    )

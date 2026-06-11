"""
Gen-3 engine-internal contracts: brain input/output + directive + mouth inputs.

Promoted VERBATIM from the validated design artifacts
(`tmp/interview_engine_research/{evidence_contract,brain_input,directive}.py`,
validated on pydantic 2.13.4). Shared enums and value-types (SignalType,
SignalPriority, CoverageState, EvidenceStance, EvidenceTexture, TimeSpan) are
imported from `interview_runtime.evidence` — the single source of truth; they
are NEVER redefined here.

Module layout:
  1. Shared vocabulary imports from evidence (single source).
  2. Brain output (LEAN): BrainMove, SignalObservation, BrainTurnOutput.
  3. Brain input (cache-split: STABLE PREFIX → DYNAMIC SUFFIX):
     BudgetPhase, SignalSpec, BankQuestionIndex, BrainSessionContext,
     ActiveQuestionRubric, SignalRead, WindowTurn, BrainTurnInput.
  4. Directive + mouth: DirectiveAct, DirectiveTone, Directive, MouthTurnInput, BridgeRequest.
  5. Brain service result: BrainDecision (loop contract; after Directive + SignalObservation).
"""

from __future__ import annotations

from enum import StrEnum

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


class FollowUpDimension(BaseModel):
    """A governed probe dimension (engine copy; mirrors interview_runtime.schemas)."""
    dimension: str
    intent: str
    seed_probe: str
    listen_for: list[str] = Field(default_factory=list)


# ============================================================================
# 1. BRAIN per-turn OUTPUT (LEAN — what the LLM emits each turn)
# ============================================================================

class BrainMove(StrEnum):
    ask = "ask"                  # advance: deliver the next main question
    probe = "probe"              # elicit specifics: one bank follow-up
    clarify = "clarify"          # candidate misunderstood → re-pose more simply
    redirect = "redirect"        # off-topic / injection → bring back on track
    reassure = "reassure"        # nervous candidate → lower the stakes
    hold = "hold"                # candidate is thinking / asked for a moment → wait, do NOT advance
    confirm = "confirm"          # garbled / possibly-misheard answer → reflect back before grading
    answer_meta = "answer_meta"  # candidate asked the agent something → answer, then return
    repeat = "repeat"            # replay the last question verbatim
    close = "close"              # terminal (full coverage / verified knockout / candidate ended)


class SignalObservation(BaseModel):
    """The brain's attribution for ONE signal it heard evidence for THIS turn (usually 0–2).

    Pointers + enums — the engine turns each of these into an immutable `EvidenceNote`, filling the
    verbatim quote, turn_ref, question-on-floor, and via_probe from data it already holds.
    `coverage_after` is the brain's LIVE read (steers the next move); it feeds runtime state, not the
    durable note.
    """
    model_config = ConfigDict(frozen=True)

    signal: str
    stance: EvidenceStance
    texture: EvidenceTexture
    coverage_after: CoverageState
    quote_span: TimeSpan | None = Field(
        default=None,
        description="Span within THIS candidate utterance that is the evidence. "
                    "None → the engine uses the whole utterance as the quote.",
    )
    retracts: bool = Field(
        default=False,
        description="True if this note walks back an earlier claim by the SAME candidate. The engine "
                    "appends a new contradicts-note and links the prior one; the prior note is KEPT.",
    )


class BrainTurnOutput(BaseModel):
    """The brain LLM's COMPLETE output for one turn. Intentionally tiny.

    The engine derives BOTH the mouth Directive (move + say, leak-scrubbed) AND the appended evidence
    notes (observations) from this single object. 'Enough evidence? probe vs advance' is encoded by
    `move` + `coverage_after`, not a separate flag.
    """
    reasoning: str = Field(
        max_length=600,
        description="Short reasoning-first scratchpad (grade↔move coherence at low effort), bounded "
                    "for latency. Audit-only: never spoken, never authoritative to the report.",
    )
    observations: list[SignalObservation] = Field(
        default_factory=list,
        description="Signals evidenced this turn (cross-question crediting allowed). "
                    "Empty on non-answer turns (clarify/meta/redirect).",
    )
    move: BrainMove
    probe_dimension: str | None = Field(
        default=None,
        description="For `probe`: the `dimension` slug of the ACTIVE question's follow-up this probe "
                    "serves. The brain composes `composed_say` WITHIN that dimension's intent. The engine "
                    "fires each dimension at most once per thread (the driver's ledger) and force-advances "
                    "at the probe cap; an already-fired or unknown slug is coerced to an unfired one, or to "
                    "`ask` when none remain. None → let the engine pick the next unfired dimension.",
    )
    preferred_next_signal: str | None = Field(
        default=None,
        description="OPTIONAL soft hint (a signal/topic) for what would flow well NEXT, for naturalness. "
                    "The deterministic resolver HONORS it only when budget has slack AND it doesn't break "
                    "the mandatory-coverage guarantee; otherwise it falls back to mandatory-first. The brain "
                    "NEVER hard-selects the next main question (resolver owns repeat + coverage + budget "
                    "safety). None → let the resolver choose by position/weight.",
    )
    composed_say: str | None = Field(
        default=None, max_length=400,
        description="Brain-composed safe text for probe/clarify/redirect/reassure/answer_meta. For `probe` "
                    "it is the targeted follow-up — a natural, in-scope adaptation of the bank follow_up "
                    "template at `probe_index` to what the candidate actually said. Leak-scrubbed before "
                    "reaching the mouth. None for ask/repeat (verbatim bank text) — and for probe it falls "
                    "back to the verbatim follow_up when not composed.",
    )
    end_requested: bool = Field(
        default=False,
        description="True ONLY when the candidate EXPLICITLY asked to end/stop the screen "
                    "(\"I'd like to end now\", \"please stop the session\"). Paired with move=close it is "
                    "honored IMMEDIATELY and BYPASSES the knockout-verification gate — a candidate may "
                    "always end the screen. Leave False for a brain-decided close (full coverage reached "
                    "or a verified knockout).",
    )
    knockout_confirmed: bool = Field(
        default=False,
        description="True ONLY when you have CONFIRMED (in-conversation: a clear disclaim, then ONE "
                    "reflect-back confirm) that a MANDATORY signal listed in `knockout_pending` is "
                    "genuinely absent. Paired with move=close it ends the screen early and RECORDS the "
                    "knockout for the report (records-never-rejects — a human still decides). The engine "
                    "honors it ONLY for a signal it actually flagged in `knockout_pending` (you cannot "
                    "fabricate a knockout). Leave False for an ordinary full-coverage close.",
    )


# ============================================================================
# 2. BRAIN INPUT — STABLE PREFIX (built once per session, byte-identical → cached)
# ============================================================================

class BudgetPhase(StrEnum):
    """The ONLY time signal the brain sees (the time arithmetic lives in the engine resolver)."""
    on_track = "on_track"          # plenty of budget — probe normally when warranted
    winding_down = "winding_down"  # little left — at most one quick elicitation, then let it advance


class SignalSpec(BaseModel):
    """One JD signal the screen collects. The FULL set is in the prefix so the brain can credit an
    answer to ANY signal (cross-question / signal-greedy crediting), even one with no dedicated Q."""
    signal: str
    signal_type: SignalType
    weight: int = Field(ge=1, le=3)
    priority: SignalPriority
    knockout: bool


class BankQuestionIndex(BaseModel):
    """Compact per-question index entry. NO rubric here on purpose — inlining all questions' rubrics
    blows the prompt (the documented ~36KB bloat). Only the ACTIVE question's rubric goes in the
    dynamic suffix."""
    question_id: str
    primary_signal: str
    signals: list[str]          # the broader coverable set
    kind: str                   # experience_check | behavioral | technical_scenario | compliance_binary
    difficulty: str             # easy | medium | hard
    is_mandatory: bool
    tier: str                   # core | coverage
    text: str
    follow_ups: list[FollowUpDimension]   # the pre-written probe dimensions


class BrainSessionContext(BaseModel):
    """STABLE PREFIX — rendered once, deterministically, byte-identical across every turn of the
    session (the prompt-cache key). Contains ZERO per-turn data. The brain's system prompt
    (instructions) is prepended to this when rendered; together they form the cached prefix."""
    job_title: str
    seniority_level: str
    role_summary: str
    hiring_bar: str
    signals: list[SignalSpec]
    bank_index: list[BankQuestionIndex]


# ============================================================================
# 2b. BRAIN INPUT — DYNAMIC SUFFIX (rebuilt each turn → the only new tokens)
# ============================================================================

class ActiveQuestionRubric(BaseModel):
    """The FULL rubric for the question on the floor — the ONLY rubric in the prompt. This is what the
    brain grades THIS answer against (verbatim, accuracy-critical). Stays out of the cached prefix
    because it changes as the active question changes."""
    question_id: str
    text: str                   # the exact bank question text
    excellent: str
    meets_bar: str
    below_bar: str
    positive_evidence: list[str]
    red_flags: list[str]
    evaluation_hint: str
    follow_ups: list[FollowUpDimension]
    fired_dimensions: list[str] = Field(
        default_factory=list,
        description="dimension slugs already fired on this thread — fire-once + cap input.",
    )


class SignalRead(BaseModel):
    """Compact running read for a signal that has been TOUCHED — the engine's ephemeral projection of
    the append-only notes (NOT in the durable contract; runtime steering only). This is the brain's
    accurate long-range memory: `coverage` + a VERBATIM key quote (no summary → no drift)."""
    signal: str
    coverage: CoverageState
    last_stance: EvidenceStance
    established_quote: str | None = Field(
        default=None,
        description="The strongest supporting (or the disclaiming) candidate quote so far, VERBATIM and "
                    "truncated — carries context that scrolled out of the transcript window. Never an LLM summary.",
    )


class WindowTurn(BaseModel):
    """A recent verbatim turn — accurate near-context. Candidate turns are DATA (fenced at render)."""
    turn_ref: str
    speaker: str                # agent | candidate
    text: str


class BrainTurnInput(BaseModel):
    """DYNAMIC SUFFIX — everything that changes per turn, appended after the cached prefix. Assembled
    deterministically by the engine. Accuracy-critical fields are verbatim; long-range context is the
    compact `evidence_so_far`. The brain reads this + the prefix and emits a `BrainTurnOutput`."""
    turn_ref: str

    # --- what's on the floor (verbatim — accuracy-critical) ---
    active_question: ActiveQuestionRubric
    on_the_floor: str = Field(
        description="The EXACT last line the agent spoke (may be a follow-up probe, not the main "
                    "question) — so clarify/repeat/confirm address the right line.",
    )
    floor_interrupted: bool = Field(
        default=False,
        description="TRUE when the question on the floor was CUT OFF mid-delivery (the agent was "
                    "interrupted by the candidate) — the candidate likely did NOT hear it. The brain "
                    "should re-deliver it (repeat) and read THIS turn as a continuation of the prior "
                    "answer, not an answer to the cut-off question.",
    )

    # --- the thing to judge this turn (DATA, never instructions) ---
    candidate_utterance: str = Field(description="The candidate's committed answer this turn, verbatim.")
    thread_turn_count: int = Field(
        ge=0, description="Turns already spent on THIS thread — anti-grind context for the probe-vs-advance call.",
    )
    stalled: bool = Field(
        default=False,
        description="TRUE when the candidate has had several CONSECUTIVE non-answer turns on this "
                    "question (dodging / re-asking / 'what's the answer' / off-task) with no gradeable "
                    "answer. Stop re-posing — advance warmly and let coverage record it as not "
                    "demonstrated. (Deterministic counter; a real answer resets it.)",
    )

    # --- context awareness (accurate + bounded) ---
    evidence_so_far: list[SignalRead] = Field(
        default_factory=list,
        description="Running read for every TOUCHED signal (long-range memory). Untouched signals are "
                    "implicitly uncovered. Compact: one line per signal, with a verbatim key quote.",
    )
    transcript_window: list[WindowTurn] = Field(
        default_factory=list,
        description="The last K turns, verbatim (near-context). Bounded for latency; the far past is "
                    "carried by evidence_so_far.",
    )

    # --- steering signals (compact) ---
    budget_phase: BudgetPhase
    uncovered_signals: list[str] = Field(
        default_factory=list,
        description="High-value signals still uncovered (weight-ranked) — focuses the brain's "
                    "cross-crediting + tells it what still matters. NOT a question picker (engine resolves that).",
    )
    knockout_pending: list[str] = Field(
        default_factory=list,
        description="Mandatory signals currently looking ABSENT — a loud flag to run the verified-knockout "
                    "flow (probe → check OR-alternatives → reflect-confirm) before concluding absence.",
    )
    knockout_reflected: list[str] = Field(
        default_factory=list,
        description="Knockout signals whose absence you have ALREADY reflected back to the candidate on a "
                    "PRIOR turn (deterministic — the engine tracks it). If a signal here is still pending and "
                    "the candidate has now affirmed the absence, CLOSE (knockout_confirmed) — do NOT reflect "
                    "it back a second time. One reflect-back is enough.",
    )


# ============================================================================
# 3. DIRECTIVE + MOUTH inputs (brain → mouth)
# ============================================================================

class DirectiveAct(StrEnum):
    ask = "ask"                  # deliver the next main question (verbatim bank text)
    probe = "probe"              # deliver one follow-up (verbatim bank follow_up)
    clarify = "clarify"          # re-pose the floor question more simply (brain-composed, leak-safe)
    redirect = "redirect"        # bring an off-topic / injection turn back (brain-composed)
    reassure = "reassure"        # lower the stakes for a nervous candidate (brain-composed)
    hold = "hold"                # candidate is thinking → a brief "take your time" (brain-composed)
    confirm = "confirm"          # reflect a possibly-misheard answer back to confirm (brain-composed)
    answer_meta = "answer_meta"  # answer a role/logistics/"are you an AI?" question (brain-composed)
    repeat = "repeat"            # replay the cached last question verbatim
    close = "close"              # warm close + next steps (terminal; composed from the act prompt)


class DirectiveTone(StrEnum):
    warm = "warm"
    neutral = "neutral"
    encouraging = "encouraging"
    calm = "calm"


class Directive(BaseModel):
    """The ONLY object that crosses brain → mouth for the REAL line. Speakable text + delivery
    metadata — NEVER a rubric. Derived by the engine from the BrainTurnOutput (move → act; the engine
    resolves `say`: the resolver's next bank question for ask, the bank follow_up[probe_index] for
    probe, or the leak-scrubbed composed_say for clarify/redirect/reassure/answer_meta)."""
    act: DirectiveAct
    say: str | None = Field(
        default=None,
        description="Verbatim words to speak. Bank question (ask) / bank follow_up (probe) / "
                    "leak-scrubbed composed_say (clarify/redirect/reassure/answer_meta). "
                    "None → the mouth composes from its act prompt (close).",
    )
    tone: DirectiveTone = DirectiveTone.warm
    spoken_setup: str | None = Field(
        default=None,
        description="Optional benign orienting clause spoken before a grounded question. Leak-safe.",
    )
    is_terminal: bool = Field(default=False, description="True only on close.")


class MouthTurnInput(BaseModel):
    """Dynamic suffix for the REAL-LINE mouth call (after the brain lands). Appended to the cached
    persona + per-act block."""
    directive: Directive
    just_said: str | None = Field(
        default=None,
        description="The BRIDGE already spoken this turn — the mouth CONTINUES from it and does NOT "
                    "re-acknowledge (one ack per turn). None if no bridge played (e.g. it failed → "
                    "a canned fallback covered it, or this is the opener).",
    )
    recent_openers: list[str] = Field(
        default_factory=list,
        description="Recent opening connectives — pick a DIFFERENT one so it never sounds stuck.",
    )


class BridgeRequest(BaseModel):
    """Input for the immediate BRIDGE mouth call — fired the instant the Ear commits the turn, in
    PARALLEL with the brain. The bridge sees ONLY the candidate's words (the brain has not decided
    yet) → it MUST be a neutral gist-mirror landing on a thinking pause, committing to NOTHING about
    answer quality or the next move (else it risks contradicting the brain). No rubric, ever."""
    candidate_utterance: str = Field(description="What the candidate just said — to mirror the gist.")
    recent_openers: list[str] = Field(default_factory=list)


# ============================================================================
# 4. BRAIN SERVICE RESULT — produced by the brain service (D3), consumed by the loop (C3)
# ============================================================================

class BrainDecision(BaseModel):
    """The brain SERVICE's per-turn result — distinct from the lean `BrainTurnOutput` LLM output.
    The service derives the `Directive` (move→act + resolver/bank/leak-scrub for `say`) and carries
    the signal `observations` for the NoteLog. The drive-loop (loop.py) consumes exactly one
    BrainDecision per committed candidate turn."""
    model_config = ConfigDict(frozen=True)

    directive: Directive
    observations: list[SignalObservation] = Field(default_factory=list)
    reasoning: str = ""          # audit-only; NEVER forwarded to the mouth (no-leak invariant)
    is_terminal: bool = False    # mirror of directive.is_terminal, for the loop's caller's convenience
    next_question_id: str | None = Field(
        default=None,
        description=(
            "When directive.act == ask: the question_id the resolver selected as the next "
            "active question. The SessionDriver uses this to advance the active-question "
            "pointer and update asked_ids. None for non-ask acts and for a close directive "
            "produced when the resolver found no remaining question."
        ),
    )
    probe_dimension: str | None = Field(
        default=None,
        description=(
            "When directive.act == probe: the coerced dimension slug the brain served "
            "(valid + unfired). The SessionDriver appends it to the thread's fired_dimensions "
            "ledger. None for non-probe acts."
        ),
    )

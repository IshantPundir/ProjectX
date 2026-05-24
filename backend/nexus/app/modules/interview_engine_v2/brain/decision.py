"""BrainDecision — the brain's structured output (one coherent reasoning pass per turn).

REASONING-FIRST (doc 13 / arXiv:2408.02442): the `reasoning` text field is field #1 so the
model speaks freely (attribute → grade → coverage → move) BEFORE committing to a move — this
buys grade↔move coherence at low reasoning_effort without the extended-thinking latency tax.

Everything here is DATA the brain emits; deterministic policy gates (brain/policy.py) validate
it, the CoverageTracker applies coverage_delta, and ControlPlane (brain/service.py) maps it to a
no-leak Directive. No rubric text ever lives in a Directive — the brain's evaluation reasoning
stays in `reasoning`/the audit record, never in say/composed_say. Strict-mode compatible
(instructor TOOLS_STRICT): all fields typed, optionals carry defaults.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class CandidateIntent(StrEnum):
    """SEMANTIC classification of the candidate turn (no regex — the model judges meaning)."""

    answer = "answer"
    clarification_request = "clarification_request"  # "what do you mean?"
    repeat_request = "repeat_request"  # "sorry, can you say that again?"
    thinking = "thinking"  # filler / formulating, not done
    no_experience = "no_experience"  # "I haven't used that"
    indirect_no = "indirect_no"  # hedge that means "no" (Indian soft-no, doc 07 §7)
    asks_question = "asks_question"  # about role/logistics (ANSWER_META path)
    off_topic = "off_topic"  # tangent / ramble
    injection = "injection"  # prompt-injection / gaming / extract-the-rubric
    wants_to_end = "wants_to_end"  # "I think I'm done"
    nervous = "nervous"  # anxious / apologizing / frozen


class BrainMove(StrEnum):
    """Closed move-set (doc 09 §3). Maps 1:1-ish to DirectiveAct in ControlPlane."""

    probe = "probe"  # one targeted follow-up (-> PROBE, verbatim bank follow_up)
    advance = "advance"  # brief neutral ack, then next question (-> ACK_ADVANCE, verbatim bank)
    clarify = "clarify"  # rephrase the current question (-> CLARIFY, composed)
    redirect = "redirect"  # bring back on-topic / injection redirect (-> REDIRECT, composed)
    hold = "hold"  # "take your time" (-> HOLD, composed)
    reassure = "reassure"  # calm a nervous candidate (-> REASSURE, composed)
    hint = "hint"  # technical nudge, never the answer (-> HINT, composed)
    answer_meta = "answer_meta"  # answer a role/logistics question, grounded (-> ANSWER_META)
    confirm = "confirm"  # reflect-to-confirm a garbled/ambiguous answer (-> CONFIRM, composed)
    repeat = "repeat"  # replay the last question (-> REPEAT, mouth uses cache)
    knockout_close = "knockout_close"  # verified knockout confirmed -> warm close (-> CLOSE)
    close = "close"  # normal end: all covered / wants-to-end (-> CLOSE terminal)


class CoverageDeltaItem(BaseModel):
    """One per-signal coverage assertion (list-of-objects so the schema is OpenAI strict-mode safe;
    a free-form dict[str,str] generates additionalProperties which strict structured-output
    rejects)."""

    signal: str = Field(description="The signal value.")
    state: str = Field(description="Target coverage state: none | partial | sufficient | failed.")


class BrainDecision(BaseModel):
    """One brain decision. `reasoning` MUST stay field #1 (see module docstring)."""

    reasoning: str = Field(
        description=(
            "Think first, in order: (1) what KIND of turn is this (intent)? (2) which signal(s) "
            "does it speak to? (3) grade the evidence vs the rubric (thin/concrete/strong + red "
            "flags); (4) what coverage state does each signal reach? (5) is the thread satisfied? "
            "(6) pick the move that is CONSISTENT with the grade. Never 'push for more' after "
            "grading an answer concrete/strong."
        )
    )
    candidate_intent: CandidateIntent
    attributed_signals: list[str] = Field(
        default_factory=list, description="Signal value(s) this turn is credited to."
    )
    grade: Literal["thin", "concrete", "strong"] | None = Field(
        default=None, description="Evidence grade vs rubric; null when the turn is not gradeable."
    )
    coverage_delta: list[CoverageDeltaItem] = Field(
        default_factory=list,
        description=(
            "Per-signal coverage updates this turn, as a list of {signal, state} items "
            "(state ∈ none|partial|sufficient|failed)."
        ),
    )
    tapped_out: bool = Field(
        default=False,
        description=(
            "True iff further probing would yield no NEW evidence (hedging, restating, 'that's "
            "about it', visible struggle). The anti-interrogation safety valve — set it honestly."
        ),
    )
    move: BrainMove
    target_signal: str | None = Field(
        default=None, description="The signal the chosen move targets, when applicable."
    )
    # ASK/PROBE: the brain SELECTS bank text by reference; ControlPlane resolves verbatim
    # (never rewrites).
    bank_question_id: str | None = Field(
        default=None, description="For `advance`: the id of the next bank question to ask."
    )
    bank_follow_up_index: int | None = Field(
        default=None, description="For `probe`: index into the active question's follow_ups."
    )
    # Composed acts (clarify/redirect/hold/reassure/hint/answer_meta/confirm/close):
    # the speakable line.
    composed_say: str | None = Field(
        default=None,
        description=(
            "Speakable text for composed moves. SPOKEN words only — NEVER rubric, evidence, "
            "red flags, or 'what I'm listening for'. Null for probe/advance/repeat."
        ),
    )
    spoken_setup: str | None = Field(
        default=None,
        description=(
            "Optional ONE benign orienting clause for a technical_scenario advance/ask: the "
            "scenario's WHAT/WHERE (e.g. 'Say tickets arrive from a system like Jira'), NEVER "
            "the HOW/solution and never a rubric term. Spoken before the question. Null for "
            "non-scenario kinds and self-contained questions."
        ),
    )
    tone: Literal["WARM", "NEUTRAL", "ENCOURAGING", "CALM"] = "NEUTRAL"
    # Verified-knockout block (doc 05). knockout_close is GATED on these by brain/policy.py.
    is_knockout: bool = Field(
        default=False, description="True when this turn confirms a mandatory signal is absent."
    )
    or_alternatives: list[str] = Field(
        default_factory=list,
        description="ALL OR-alternative signals for the mandatory requirement under test.",
    )
    or_alternatives_checked: bool = Field(
        default=False,
        description=(
            "True iff EVERY OR-alternative was asked about and found absent (the b99d8cc6 guard)."
        ),
    )
    reflect_confirmed: bool = Field(
        default=False,
        description=(
            "True iff the absence was reflect-to-confirmed (a real 'no', not a hedge / STT error)."
        ),
    )
    # ANSWER_META grounding (doc 05): only answer role questions from context; else defer.
    answer_meta_grounded: bool = Field(
        default=True,
        description=(
            "False when the asked role detail is NOT in context — composed_say must then defer "
            "to the recruiter."
        ),
    )

    def coverage_map(self) -> dict[str, str]:
        """List-of-items → {signal: state} (the shape CoverageTracker/policy consume)."""
        return {item.signal: item.state for item in self.coverage_delta}

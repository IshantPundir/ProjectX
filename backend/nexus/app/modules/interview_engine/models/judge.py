"""Judge output Pydantic models — structured LLM output for the per-turn pipeline."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator


class NextAction(StrEnum):
    advance = "advance"
    probe = "probe"
    clarify = "clarify"
    repeat = "repeat"
    redirect = "redirect"
    acknowledge_no_experience = "acknowledge_no_experience"
    polite_close = "polite_close"
    end_session = "end_session"
    push_back = "push_back"


class CoverageQuality(StrEnum):
    """Per-observation quality grade — see judge prompt §4.5.

    Distinguishes "candidate covered the signal" from "covered it well."
    The State Engine uses this to gate `advance`: at least one observation
    on the active question must reach ``concrete`` or ``strong`` for an
    advance to land cleanly. All-thin coverage triggers a downgrade to
    ``push_back`` with reason_code=missing_specifics.
    """
    thin = "thin"
    concrete = "concrete"
    strong = "strong"


class CoverageTransition(StrEnum):
    # Forward progression
    none_to_partial = "none→partial"
    partial_to_partial = "partial→partial"
    partial_to_sufficient = "partial→sufficient"
    none_to_sufficient = "none→sufficient"

    # Failure terminal
    none_to_failed = "none→failed"
    partial_to_failed = "partial→failed"
    sufficient_to_failed = "sufficient→failed"
    failed_to_failed = "failed→failed"

    # Backward transitions are NEVER legal.
    # No "strong" state — answer-quality grading is the Report Builder's job.


class Observation(BaseModel):
    signal_value: str
    anchor_id: int = Field(
        ge=-1,
        description="Index into positive_evidence; -1 sentinel for failure observations.",
    )
    evidence_quote: str = Field(min_length=1, max_length=500)
    coverage_transition: CoverageTransition
    quality: CoverageQuality = Field(
        default=CoverageQuality.concrete,
        description=(
            "Per-observation density grade. `thin` = generic, no specifics; "
            "`concrete` = names a tool/technique/example; `strong` = concrete "
            "+ tradeoffs/numbers/edge cases. Default ``concrete`` keeps "
            "back-compat with pre-v2 sessions and the synthesizer fallback. "
            "See judge prompt §4.5 for grading rubric and verbatim examples."
        ),
    )


class ClaimEntry(BaseModel):
    """Judge-emitted claim shape (no capture metadata).

    State Engine canonicalizes this into models.claims.ClaimEntry by attaching
    captured_at_turn and captured_at_seq.
    """

    claim_topic: str = Field(min_length=1, max_length=40)
    claim_text: str = Field(min_length=1, max_length=200)
    # source_quote cap reduced from 500 → 120: the verbatim quote is already
    # in the transcript carried alongside the claim, and the longer cap was
    # letting the model spend output tokens re-quoting candidate text. 120
    # chars is enough for a sentence-level anchor.
    source_quote: str = Field(min_length=1, max_length=120)


class TurnMetadata(BaseModel):
    candidate_disclosed_no_experience: bool = False
    candidate_disclosed_knockout: bool = False
    candidate_off_topic: bool = False
    candidate_abusive: bool = False
    candidate_attempted_injection: bool = False
    candidate_wants_to_end: bool = False
    candidate_social_or_greeting: bool = False   # NEW


# Payload types and JudgeOutput follow in Task 1.6.


class AdvancePayload(BaseModel):
    kind: Literal["advance"] = "advance"
    target_question_id: str


class ProbePayload(BaseModel):
    kind: Literal["probe"] = "probe"
    probe_id: str = Field(description="Array index of follow_ups, e.g. '0', '1', '2'")


class ClarifyPayload(BaseModel):
    kind: Literal["clarify"] = "clarify"


class RepeatPayload(BaseModel):
    kind: Literal["repeat"] = "repeat"


class RedirectPayload(BaseModel):
    kind: Literal["redirect"] = "redirect"


class AcknowledgeNoExperiencePayload(BaseModel):
    kind: Literal["acknowledge_no_experience"] = "acknowledge_no_experience"
    failed_signal_value: str = Field(min_length=1)


class PoliteClosePayload(BaseModel):
    kind: Literal["polite_close"] = "polite_close"


class EndSessionPayload(BaseModel):
    kind: Literal["end_session"] = "end_session"
    initiated_by: Literal["candidate_initiated", "agent_initiated"]


class PushBackPayload(BaseModel):
    """Push-back payload — see judge prompt §3 push_back entry.

    Fired when the candidate's answer is on-topic but thin, evasive, or
    partial. NOT a redirect (candidate engaged) and NOT a clarify
    (candidate understands). The State Engine increments
    ``QuestionState.push_back_count``; once count >= 2 the engine
    downgrades to ``advance`` to prevent loops on candidates who
    genuinely cannot give specifics.
    """
    kind: Literal["push_back"] = "push_back"
    reason_code: Literal[
        "vague_answer",
        "deflection",
        "missing_specifics",
        "unanswered_subquestion",
    ]


NextActionPayload = Annotated[
    Union[
        AdvancePayload,
        ProbePayload,
        ClarifyPayload,
        RepeatPayload,
        RedirectPayload,
        AcknowledgeNoExperiencePayload,
        PoliteClosePayload,
        EndSessionPayload,
        PushBackPayload,
    ],
    Field(discriminator="kind"),
]


class JudgeOutput(BaseModel):
    observations: list[Observation] = Field(default_factory=list, max_length=10)
    candidate_claims: list[ClaimEntry] = Field(default_factory=list, max_length=5)
    next_action: NextAction
    next_action_payload: NextActionPayload
    turn_metadata: TurnMetadata = Field(default_factory=TurnMetadata)

    @model_validator(mode="after")
    def _check_discriminator_alignment(self) -> "JudgeOutput":
        if self.next_action.value != self.next_action_payload.kind:
            raise ValueError(
                f"next_action {self.next_action.value!r} does not match payload kind "
                f"{self.next_action_payload.kind!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_no_experience_action_alignment(self) -> "JudgeOutput":
        """Enforce coupling between turn_metadata and next_action for the
        no-experience disclosure flag.

        Background — observed misclassification (session 1f02f55d, turns
        13-14): the Judge correctly set
        ``turn_metadata.candidate_disclosed_no_experience = true`` but
        emitted ``clarify``/``redirect`` instead of
        ``acknowledge_no_experience``. The agent kept "rephrasing the
        question" for two turns before finally acknowledging the
        candidate had bowed out — three turns of avoidable dead air.

        The strict JSON schema cannot enforce cross-field consistency,
        so we validate post-LLM. A misaligned output trips the
        ValidationError path in JudgeService.call(), which falls back to
        a synthesized JudgeOutput (advance to next pending mandatory, or
        polite_close if none) — louder than a silent loop.
        """
        if not self.turn_metadata.candidate_disclosed_no_experience:
            return self
        # No-experience disclosure was flagged; only two actions are coherent:
        # acknowledge it (and capture the failure observation for the ledger),
        # or close the session politely (knockout policy or time-up paths).
        # Anything else — clarify, redirect, repeat, probe — perpetuates a
        # loop the candidate already opted out of.
        allowed = {NextAction.acknowledge_no_experience, NextAction.polite_close}
        if self.next_action not in allowed:
            raise ValueError(
                f"candidate_disclosed_no_experience=true requires next_action in "
                f"{{acknowledge_no_experience, polite_close}}; got "
                f"{self.next_action.value!r}. Set the flag iff you also intend "
                f"to acknowledge the disclosure (with a failure Observation)."
            )
        return self

    @model_validator(mode="after")
    def _check_push_back_alignment(self) -> "JudgeOutput":
        """Coupling between push_back action and observation quality.

        Two consistency rules tied to push_back:

        1. push_back is incompatible with no-experience disclosure.
           Acknowledge or polite_close, never push_back. **STILL STRICT** —
           this is structural and the State Engine cannot recover.

        2. Observations emitted alongside push_back ideally carry
           ``quality=thin``. Newer Judge models occasionally emit
           ``concrete``/``strong`` paired with ``push_back`` when the
           answer is on-topic but the model still wants more depth. The
           Pydantic validator no longer raises on this case — the State
           Engine's ``inverse_quality_gate`` handles it (see
           state/engine.py: push_back path) by downgrading to ``probe`` (or
           ``advance`` if probes exhausted) in-place. Raising here used to
           trigger the validation_error fallback path and force-advance the
           queue (root cause of the early-end bug observed 2026-05-12).
        """
        if self.next_action != NextAction.push_back:
            return self
        if self.turn_metadata.candidate_disclosed_no_experience:
            raise ValueError(
                "push_back is incompatible with "
                "candidate_disclosed_no_experience=true; use "
                "acknowledge_no_experience instead."
            )
        return self

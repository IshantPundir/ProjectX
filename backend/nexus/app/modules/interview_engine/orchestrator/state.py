"""InterviewState — the Orchestrator's state-machine record.

Held in the ``StructuredInterviewAgent`` instance during a session and
persisted to Redis on every phase change for reconnect support
(A.4 / B). Pure code: no I/O, no LLM, no DB.

The phase-transition map below is the canonical state machine from the
design doc §5.1 / §6:

```
connecting → consent → intro → main_loop
main_loop → knockout_confirmation
            → early_exit_wrap        (confirmed disclaim)
            → main_loop              (corrected by candidate)
main_loop → normal_wrap              (all mandatory sufficient OR time)
main_loop → candidate_initiated_wrap (candidate ends pause-decline path)
{*_wrap}  → closed
{any}     → closed                    (technical failure)
```

Illegal transitions raise ``InterviewPhaseError``. The
``transition()`` helper is the single mutation entry point so the
sequence-number-and-mtime persistence layer (A.4) has a single hook.

------------------------------------------------------------------
**Mutation discipline — please read before modifying this file.**

``InterviewState`` and ``QuestionState`` are Pydantic models with
default mutability (``model_config`` does NOT set ``frozen=True`` or
``validate_assignment=True``). Direct field assignment such as
``state.phase = InterviewPhase.MAIN_LOOP`` or ``state.reconnect_count
+= 1`` is syntactically permitted by Python but **silently bypasses
both the legality checks and the sequence-number bumping** that the
named methods perform.

**Always mutate through the named methods:**

* ``state.transition(target)`` — phase changes; rejects illegal
  transitions; bumps ``sequence_number``.
* ``state.record_reconnect()`` — increments ``reconnect_count``;
  rejects mutation of a CLOSED session; bumps ``sequence_number``.
* ``state.set_exit_mode(mode, ended_at=...)`` — stamps exit mode +
  end-time exactly once; bumps ``sequence_number``.
* ``QuestionState`` field updates during a turn (e.g. incrementing
  ``followups_asked`` or ``meta_request_count``) are direct
  assignments today — those don't need legality checks. If a
  per-question invariant ever needs enforcement, add a method here
  rather than letting direct mutation accumulate.

The ``LedgerPersistence`` layer (A.4) reads ``state.sequence_number``
to detect lost Redis writes; bypassing the mutation methods produces
silent gap-detection failures (the seq doesn't advance, so the
persistence layer thinks no write was needed).

This is enforced by convention, not Python. A future contributor
reading ``state.phase`` and writing it back without going through
``transition()`` is the exact bug class this docstring exists to
prevent. If you're tempted: don't.
------------------------------------------------------------------
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class InterviewPhase(StrEnum):
    CONNECTING = "connecting"
    CONSENT = "consent"
    INTRO = "intro"
    MAIN_LOOP = "main_loop"
    KNOCKOUT_CONFIRMATION = "knockout_confirmation"
    EARLY_EXIT_WRAP = "early_exit_wrap"
    NORMAL_WRAP = "normal_wrap"
    CANDIDATE_INITIATED_WRAP = "candidate_initiated_wrap"
    CLOSED = "closed"


class ExitMode(StrEnum):
    """Maps onto the existing `SessionOutcome` participant attribute.

    - COMPLETED → "completed"
    - KNOCKOUT_EXIT → "candidate_ended" (with non-empty knockout_failures)
    - CANDIDATE_INITIATED_EXIT → "candidate_ended" (empty knockout_failures)
    - TECHNICAL_FAILURE → "candidate_disconnected" or "error"

    The mapping is finalized in B (`structured_agent.py` close handler).
    """

    COMPLETED = "completed"
    KNOCKOUT_EXIT = "knockout_exit"
    CANDIDATE_INITIATED_EXIT = "candidate_initiated_exit"
    TECHNICAL_FAILURE = "technical_failure"


AskedMode = Literal["standard", "deepening", "skipped"]


# Canonical phase-transition allowlist. Every other transition raises.
# CLOSED is reachable from every non-CLOSED phase (technical-failure /
# error path); enumerated explicitly so the test surface catches drift.
_LEGAL_TRANSITIONS: dict[InterviewPhase, frozenset[InterviewPhase]] = {
    InterviewPhase.CONNECTING: frozenset({
        InterviewPhase.CONSENT, InterviewPhase.CLOSED,
    }),
    InterviewPhase.CONSENT: frozenset({
        InterviewPhase.INTRO, InterviewPhase.CLOSED,
    }),
    InterviewPhase.INTRO: frozenset({
        InterviewPhase.MAIN_LOOP, InterviewPhase.CLOSED,
    }),
    InterviewPhase.MAIN_LOOP: frozenset({
        InterviewPhase.KNOCKOUT_CONFIRMATION,
        InterviewPhase.NORMAL_WRAP,
        InterviewPhase.CANDIDATE_INITIATED_WRAP,
        InterviewPhase.CLOSED,
    }),
    InterviewPhase.KNOCKOUT_CONFIRMATION: frozenset({
        InterviewPhase.EARLY_EXIT_WRAP,
        InterviewPhase.MAIN_LOOP,  # candidate corrected during confirmation
        InterviewPhase.CLOSED,
    }),
    InterviewPhase.EARLY_EXIT_WRAP: frozenset({InterviewPhase.CLOSED}),
    InterviewPhase.NORMAL_WRAP: frozenset({InterviewPhase.CLOSED}),
    InterviewPhase.CANDIDATE_INITIATED_WRAP: frozenset({InterviewPhase.CLOSED}),
    InterviewPhase.CLOSED: frozenset(),  # terminal
}


class InterviewPhaseError(RuntimeError):
    """Illegal phase transition (or mutation of a CLOSED session)."""


class QuestionState(BaseModel):
    """Per-question runtime state.

    Tracks ask-time / completion-time, follow-up budget consumption,
    meta-request budget consumption, and which template was used to
    deliver the question.
    """

    question_id: str = Field(min_length=1)
    position: int = Field(ge=0)
    is_mandatory: bool
    asked_at: datetime | None = None
    completed_at: datetime | None = None
    followups_asked: int = Field(default=0, ge=0)
    meta_request_count: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    asked_mode: AskedMode | None = None


class InterviewState(BaseModel):
    """The Orchestrator's state-machine record for a single session.

    Persisted to Redis on every phase change (A.4) so a fresh agent
    instance after a reconnect can rehydrate.

    Identity fields use ``str`` (UUID-as-string) to keep the Redis-side
    JSON wire-format stable across Python / non-Python consumers.

    `prompt_versions` records the per-template version pinned at session
    start (e.g. ``{"speech_agent.intro": "v1", "speech_agent.ask_question_standard": "v1"}``).
    Once a session starts, those versions never change — see design doc
    §7.19.

    `model_versions` records the model IDs in use for each role
    (``llm``, ``stt``, ``tts``, ``evaluator_intent``, ``evaluator_disclaim``,
    ``evaluator_sufficiency``). Pinned at session start.

    `turn_log` is intentionally NOT a field on this model — turn-by-turn
    audit goes through the `EventCollector` envelope (already wired in
    `agent.py`); duplicating it here would double Redis pressure with
    no observability gain.
    """

    # ---- identity ----
    session_id: str
    tenant_id: str
    job_id: str
    candidate_id: str

    # ---- phase ----
    phase: InterviewPhase = InterviewPhase.CONNECTING
    started_at: datetime
    ended_at: datetime | None = None
    exit_mode: ExitMode | None = None

    # ---- question progression ----
    current_question_id: str | None = None
    questions: list[QuestionState] = Field(default_factory=list)

    # ---- time budget ----
    target_duration_seconds: int = Field(gt=0)
    in_compression_mode: bool = False

    # ---- knockout state ----
    pending_knockout_signal_value: str | None = None
    knockout_confirmation_attempted: bool = False

    # ---- reconnect ----
    reconnect_count: int = Field(default=0, ge=0)
    max_reconnects: int = Field(default=2, ge=0)

    # ---- versions (pinned at session start) ----
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    ledger_schema_version: str = "ledger.v1"
    model_versions: dict[str, str] = Field(default_factory=dict)

    # ---- monotonic write counter for Redis gap detection (A.4) ----
    sequence_number: int = 0

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def transition(self, target: InterviewPhase) -> None:
        """Move to ``target`` if the transition is legal; else raise.

        Increments ``sequence_number`` exactly once. The single-entry-
        point design lets the persistence layer (A.4) hook here and
        flush to Redis fire-and-forget on every phase change.
        """
        if target not in _LEGAL_TRANSITIONS[self.phase]:
            raise InterviewPhaseError(
                f"Illegal phase transition: {self.phase.value!r} → "
                f"{target.value!r} (allowed: "
                f"{sorted(p.value for p in _LEGAL_TRANSITIONS[self.phase])})"
            )
        self.phase = target
        self.sequence_number += 1

    def record_reconnect(self) -> None:
        """Increment reconnect counter.

        Caller is expected to verify ``reconnect_count < max_reconnects``
        before calling — exceeding the cap is the trigger for transition
        to ``CLOSED`` via the technical-failure path (handled in B).
        """
        if self.phase == InterviewPhase.CLOSED:
            raise InterviewPhaseError(
                "Cannot record reconnect on a CLOSED session"
            )
        self.reconnect_count += 1
        self.sequence_number += 1

    def set_exit_mode(self, mode: ExitMode, *, ended_at: datetime) -> None:
        """Stamp the exit mode + end-time.

        Called once during the wrap → CLOSED transition. Idempotent only
        if called with the same arguments; a second call with a different
        ``ended_at`` raises (a session ends exactly once).
        """
        if self.exit_mode is not None and (
            self.exit_mode != mode or self.ended_at != ended_at
        ):
            raise InterviewPhaseError(
                f"Exit mode already set to {self.exit_mode.value!r}; "
                f"cannot reassign to {mode.value!r}"
            )
        self.exit_mode = mode
        self.ended_at = ended_at
        self.sequence_number += 1

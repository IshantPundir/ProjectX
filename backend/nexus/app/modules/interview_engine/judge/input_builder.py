"""Judge input builder — assembles structured input for the Judge LLM call.

Active-question-only scope: the Judge sees rubric content for the active
question only. Cross-question evidence is captured in transcript and handled
post-session by the Report Builder.

Field order is deliberately STABLE-FIRST → DYNAMIC-LAST so the JSON byte
prefix sent to OpenAI is cache-friendly:

    1. active_question_*           (changes only on advance — ~every 3-4 turns)
    2. next_pending_question_*     (changes only on advance)
    3. active_question_remaining_probes (changes only on probe consumption)
    4. signal_coverage             (current coverage per signal — can change per turn)
    5. candidate_claims            (grows when claims emitted — can change per turn)
    6. recent_turns                (changes every turn; bounded slice)
    7. candidate_utterance         (changes every turn)
    8. time_remaining_seconds      (changes every turn)

The full append-only ledger entries[] and the full QuestionQueueSnapshot were
removed: the Judge only needs current per-signal coverage and the next
pending question ID, both of which the orchestrator resolves before the
call. Forensic state (entries, per-question turn counters, time_spent_ms)
lives on the State Engine and the SessionResult — not on the LLM input.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimEntry
from app.modules.interview_engine.models.ledger import (
    SignalLedgerSnapshot, SignalSnapshot,
)
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_runtime import (
    QuestionConfig, TranscriptEntry,
)


class ActiveSignalMeta(BaseModel):
    """Per-signal metadata projected from SessionConfig.signal_metadata.

    Surfaces the knockout flag, priority, and type for the *active*
    question's signals so the Judge can phrase its reasoning with the
    right context (e.g., experience-class signals vs. competency-class
    inform the first-I-don't-know disambiguation rule). Enforcement of
    knockout policy still happens deterministically at the State Engine
    layer; this is purely informational.
    """

    value: str
    type: Literal["competency", "experience", "credential", "behavioral"]
    knockout: bool
    priority: Literal["required", "preferred"]


class JudgeInputPayload(BaseModel):
    """Structured input passed to the Judge LLM (rendered into prompt JSON).

    Field declaration order matches the cache-stability ordering documented
    in the module docstring. Pydantic's ``model_dump_json`` honors
    declaration order, so this controls the byte-prefix the model sees.
    """

    # --- STABLE per-question (cacheable across the active question's turns) ---
    active_question_id: str | None
    active_question_text: str | None
    active_question_positive_evidence: list[str] = Field(default_factory=list)
    active_question_red_flags: list[str] = Field(default_factory=list)
    active_question_rubric: dict[str, str] = Field(default_factory=dict)
    active_question_evaluation_hint: str | None = None
    active_question_difficulty: Literal["easy", "medium", "hard"] | None = Field(
        default=None,
        description=(
            "Difficulty of the active question. Calibrates grading strictness: "
            "on 'easy', accept an engaged answer even if thin; on 'hard', "
            "demand concrete depth (tradeoffs/numbers) before advancing. The "
            "State Engine enforces the advance gate and push-back cap "
            "deterministically; this is grading guidance for the Judge."
        ),
    )
    active_question_signal_metadata: list[ActiveSignalMeta] = Field(
        default_factory=list,
        description=(
            "Per-signal metadata for the active question's signals — carries "
            "the knockout flag so the Judge can identify which ?->failed "
            "observations are session-ending. Enforcement is still done by "
            "the State Engine; this is informational."
        ),
    )

    # --- Semi-stable — changes only when the queue advances or a probe is consumed ---
    next_pending_question_id: str | None = Field(
        default=None,
        description=(
            "The question_id the Judge MUST use as target_question_id when "
            "emitting `advance`. Pre-resolved by the State Engine — may be a "
            "mandatory question OR a non-mandatory one (when mandatory queue "
            "exhausted and uncovered signals remain). The Judge does not "
            "decide selection; it just advances to whatever is provided."
        ),
    )
    next_pending_question_is_mandatory: bool | None = Field(
        default=None,
        description=(
            "True if next_pending_question_id is mandatory, False if "
            "non-mandatory (selected because its signals are still "
            "uncovered), None if no next question exists (→ polite_close)."
        ),
    )
    active_question_push_back_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of push_back actions already applied to the active "
            "question. Hard cap is per-difficulty (easy 1 / medium 2 / "
            "hard 3) enforced server-side; an over-cap push_back is "
            "downgraded to advance. At the cap, prefer probe (if probes "
            "remain) or advance — do NOT emit push_back again."
        ),
    )
    active_question_still_confused_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Consecutive turns on the active question where the Judge set "
            "turn_metadata.candidate_still_confused=true (generic confusion "
            "/ cannot engage). Resets to 0 on any other turn or on advance. "
            "The State Engine acknowledges and moves on automatically once "
            "this reaches 2 — the Judge does NOT decide that escalation; it "
            "keeps emitting clarify + candidate_still_confused and the engine "
            "owns the cap."
        ),
    )
    active_question_remaining_probes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Remaining probes for the active question — keys are probe_ids, "
            "values are the probe text. Probes that have already been "
            "consumed are NOT in this dict, so the Judge cannot pick a "
            "consumed probe id."
        ),
    )

    # --- Dynamic — changes per turn ---
    signal_coverage: dict[str, SignalSnapshot] = Field(
        default_factory=dict,
        description=(
            "Current coverage state per signal_value. Replaces the full "
            "SignalLedgerSnapshot — the append-only entries[] list was "
            "audit-only data the Judge never needed for decision-making."
        ),
    )
    candidate_claims: list[ClaimEntry] = Field(
        default_factory=list,
        description="Biographical claims captured so far this session (capped pool).",
    )
    recent_turns: list[TranscriptEntry] = Field(
        default_factory=list,
        description=(
            "Bounded slice of the conversation transcript (last N entries; "
            "the orchestrator caps this — the State Engine still owns the "
            "full transcript for the SessionResult)."
        ),
    )
    candidate_utterance: str
    time_remaining_seconds: int


def build_judge_input(
    *,
    active_question: QuestionConfig | None,
    ledger_snapshot: SignalLedgerSnapshot,
    queue_snapshot: QuestionQueueSnapshot,
    claims_snapshot: ClaimsPoolSnapshot,
    recent_turns: list[TranscriptEntry],
    candidate_utterance: str,
    time_remaining_seconds: int,
    next_pending_question: tuple[str, bool] | None,
    active_signal_metadata: list[ActiveSignalMeta] | None = None,
    active_remaining_probes: dict[str, str] | None = None,
    active_question_push_back_count: int = 0,
    active_question_still_confused_count: int = 0,
) -> JudgeInputPayload:
    """Project the State Engine's full snapshots into the slim JudgeInputPayload.

    Why drop ledger.entries[] and the full queue snapshot:
      * The Judge consumes only current coverage (``snapshots`` field) for
        decision-making; ``entries`` is forensic data that grew ~30 tok/turn
        in older sessions for no decision benefit.
      * The Judge consumes only the active question and the next pending
        question ID. Per-question turn counters and time_spent_ms grew the
        prompt without affecting any decision.

    ``next_pending_question`` is a tuple ``(question_id, is_mandatory)`` or
    None. When not None, the question_id may be mandatory OR non-mandatory
    (when the mandatory queue is exhausted and uncovered signals remain).
    The Judge does not decide selection; the State Engine already applied
    the selection rule.
    """
    next_id: str | None = None
    next_is_mandatory: bool | None = None
    if next_pending_question is not None:
        next_id, next_is_mandatory = next_pending_question

    return JudgeInputPayload(
        active_question_id=active_question.id if active_question else None,
        active_question_text=active_question.text if active_question else None,
        active_question_positive_evidence=(
            list(active_question.positive_evidence) if active_question else []
        ),
        active_question_red_flags=(
            list(active_question.red_flags) if active_question else []
        ),
        active_question_rubric=(
            active_question.rubric.model_dump() if active_question else {}
        ),
        active_question_evaluation_hint=(
            active_question.evaluation_hint if active_question else None
        ),
        active_question_difficulty=(
            active_question.difficulty if active_question else None
        ),
        active_question_signal_metadata=list(active_signal_metadata or []),
        next_pending_question_id=next_id,
        next_pending_question_is_mandatory=next_is_mandatory,
        active_question_push_back_count=active_question_push_back_count,
        active_question_still_confused_count=active_question_still_confused_count,
        active_question_remaining_probes=dict(active_remaining_probes or {}),
        signal_coverage=dict(ledger_snapshot.snapshots),
        candidate_claims=list(claims_snapshot.entries),
        recent_turns=list(recent_turns),
        candidate_utterance=candidate_utterance,
        time_remaining_seconds=time_remaining_seconds,
    )

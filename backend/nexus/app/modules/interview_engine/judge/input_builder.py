"""Judge input builder — assembles structured input for the Judge LLM call.

Active-question-only scope: the Judge sees rubric content for the active
question only. Cross-question evidence is captured in transcript and handled
post-session by the Report Builder.

Field order is deliberately STABLE-FIRST → DYNAMIC-LAST so the JSON byte
prefix sent to OpenAI is cache-friendly:

    1. active_question_*           (changes only on advance — ~every 3-4 turns)
    2. next_pending_mandatory_*    (changes only on advance)
    3. active_question_remaining_probes (changes only on probe consumption)
    4. signal_coverage             (current coverage per signal — can change per turn)
    5. candidate_claims            (grows when claims emitted — can change per turn)
    6. recent_turns                (changes every turn; bounded slice)
    7. candidate_utterance         (changes every turn)
    8. time_remaining_seconds      (changes every turn)

The full append-only ledger entries[] and the full QuestionQueueSnapshot were
removed: the Judge only needs current per-signal coverage and the next
pending mandatory ID, both of which the orchestrator resolves before the
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
    type: Literal["experience", "credential", "competency", "behavioral"]
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
    next_pending_mandatory_question_id: str | None = Field(
        default=None,
        description=(
            "The question_id the Judge MUST use as target_question_id when "
            "emitting `advance`. Resolved by the orchestrator from the queue "
            "before the call so the Judge does not have to walk a queue "
            "snapshot itself."
        ),
    )
    active_question_push_back_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of push_back actions already applied to the active "
            "question. Hard cap = 2 enforced server-side (a 3rd incoming "
            "push_back is downgraded to advance). The Judge prompt §3 "
            "push_back entry instructs: when this is 2, prefer probe "
            "(if probes remain) or advance — do NOT emit push_back again."
        ),
    )
    active_question_consecutive_dont_know_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Consecutive 'I don't know' / 'I'm not sure' / 'no idea' "
            "candidate utterances on the active question (resets on any "
            "substantive answer or question advance). The Judge prompt §3 "
            "acknowledge_no_experience entry instructs: when this is >= 1 "
            "AND the active signal is experience/credential class, prefer "
            "acknowledge_no_experience over clarify — the candidate has "
            "signaled they cannot answer; further clarify will loop "
            "(observed in session f665498d turns 14-18)."
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
    next_pending_mandatory_id: str | None,
    active_signal_metadata: list[ActiveSignalMeta] | None = None,
    active_remaining_probes: dict[str, str] | None = None,
    active_question_push_back_count: int = 0,
    active_question_consecutive_dont_know_count: int = 0,
) -> JudgeInputPayload:
    """Project the State Engine's full snapshots into the slim JudgeInputPayload.

    Why drop ledger.entries[] and the full queue snapshot:
      * The Judge consumes only current coverage (``snapshots`` field) for
        decision-making; ``entries`` is forensic data that grew ~30 tok/turn
        in older sessions for no decision benefit.
      * The Judge consumes only the active question and the next pending
        mandatory ID. Per-question turn counters and time_spent_ms grew the
        prompt without affecting any decision.
    """
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
        active_question_signal_metadata=list(active_signal_metadata or []),
        next_pending_mandatory_question_id=next_pending_mandatory_id,
        active_question_push_back_count=active_question_push_back_count,
        active_question_consecutive_dont_know_count=active_question_consecutive_dont_know_count,
        active_question_remaining_probes=dict(active_remaining_probes or {}),
        signal_coverage=dict(ledger_snapshot.snapshots),
        candidate_claims=list(claims_snapshot.entries),
        recent_turns=list(recent_turns),
        candidate_utterance=candidate_utterance,
        time_remaining_seconds=time_remaining_seconds,
    )

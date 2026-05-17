"""Pydantic payload schemas for engine audit event kinds.

Every event written via EventCollector.append uses one of these payload shapes.
The collector itself doesn't validate — these models are for type discipline at
the call sites (orchestrator, JudgeService, SpeakerService) and for parsing
audit envelopes downstream.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# Turn boundaries
class TurnStartedPayload(BaseModel):
    turn_id: str
    turn_index: int = Field(ge=0)
    stt_text_raw: str | None = None     # verbatim Deepgram output
    stt_text_used: str | None = None    # what the Judge sees (= raw in v1)


class TurnCompletedPayload(BaseModel):
    turn_id: str
    turn_index: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class TurnCoalescedPayload(BaseModel):
    """Audit payload for a coalesced turn — the new turn's candidate text was
    prepended with the prior turn's text before the Judge call.

    Two coalescing gates can fire (see ``_should_coalesce``):
    * ``"coalesced"`` — the prior turn's Speaker did not deliver its body
      (interrupted before / during body streaming, or empty Speaker output).
    * ``"coalesced_pre_body"`` — the prior turn's Speaker DID deliver its
      body, but the candidate's new utterance ended before that body became
      audible, so it cannot be a reply to it.

    See ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
    """
    prior_turn_id: str
    current_turn_id: str
    prior_text: str             # prior turn's candidate utterance (redacted to length+hash in metadata mode)
    current_text: str           # new turn's candidate utterance pre-merge
    combined_text: str          # what the Judge actually sees
    prior_instruction_kind: str # InstructionKind.value as string
    prior_sub_context: str      # sub-context discriminator string ("default" if none)
    gap_ms: int = Field(ge=0)   # ms between prior TURN_COMPLETED and this TURN_STARTED
    # ms between the candidate's most recent silence onset and this TURN_STARTED.
    # Non-None ONLY when the silence-aware window reference was load-bearing
    # (i.e., more recent than prior TURN_COMPLETED). When None, gap_ms above
    # tells the full window-check story.
    silence_gap_ms: int | None = Field(default=None, ge=0)
    coalesce_window_ms: int = Field(ge=1, le=30000)  # config snapshot for audit clarity
    reason: Literal["coalesced", "coalesced_pre_body"] = "coalesced"


class TurnDroppedPayload(BaseModel):
    """Audit payload for a stale-turn drop.

    Fired when on_user_turn_completed receives a fragment whose
    stopped_speaking_at is past the configured staleness threshold AND
    a more-recent silence onset has been observed (evidence that a
    fresher turn is queued behind this one). The text is buffered for
    the next non-dropped turn; no Judge/Speaker call runs.
    """
    turn_id: str            # ID we would have used had we processed this
    candidate_text: str     # the dropped fragment (redacted to length+hash in metadata mode)
    stopped_speaking_at: float | None  # wall-clock when candidate stopped this fragment
    staleness_ms: int = Field(ge=0)
    buffer_size_after: int = Field(ge=1)


class TurnDrainReplayedPayload(BaseModel):
    """Audit payload for buffer drain into a non-dropped turn.

    Fired on the next non-dropped turn when one or more buffered stale
    texts are prepended to ``candidate_text`` BEFORE the coalesce gate
    runs. Records the order and content of drained fragments so replay
    tooling can reconstruct the merge sequence.
    """
    current_turn_id: str
    dropped_count: int = Field(ge=1)
    dropped_texts: list[str]  # in original drop order (oldest first)
    combined_text: str        # final candidate_text the coalesce gate then sees


# Judge
class JudgeCallPayload(BaseModel):
    turn_id: str
    model: str
    prompt_hash: str
    input_summary: dict[str, Any]
    output: dict[str, Any]              # JudgeOutput.model_dump(mode="json")
    latency_ms: int = Field(ge=0)
    usage: dict[str, int] | None = None  # {"prompt_tokens": …, "completion_tokens": …}


class JudgeSyntheticPayload(BaseModel):
    turn_id: str
    output: dict[str, Any]
    reason: Literal["session_start", "pre_filter_repeat"] = "session_start"


class JudgeFallbackPayload(BaseModel):
    turn_id: str
    reason: Literal["timeout", "parse_error", "validation_error", "no_advance_target"]
    original_failure_context: dict[str, Any]
    synthesized_output: dict[str, Any]


class JudgeValidationPayload(BaseModel):
    turn_id: str
    level: Literal["warning", "error"]
    code: str
    details: dict[str, Any]


# State mutations
class StateMutationPayload(BaseModel):
    turn_id: str
    seq: int = Field(ge=1)
    kind: Literal[
        "ledger.append", "queue.advance", "queue.probe", "queue.complete",
        "claims.add", "claims.drop_oldest",
        "lifecycle.transition", "knockout.recorded",
    ]
    before: dict[str, Any] | None
    after: dict[str, Any]


# Speaker
class SpeakerCallPayload(BaseModel):
    turn_id: str
    model: str
    prompt_hash: str
    instruction_kind: str
    bank_text_present: bool
    latency_ms_first_token: int = Field(ge=0)
    latency_ms_total: int = Field(ge=0)
    usage: dict[str, int] | None = None
    final_utterance: str


class SpeakerCachedPayload(BaseModel):
    turn_id: str
    instruction_kind: Literal["repeat"]
    source_turn_id: str
    final_utterance: str


class SpeakerOutputPayload(BaseModel):
    turn_id: str
    final_utterance: str


class SpeakerInputPayload(BaseModel):
    """Audit payload capturing what the Speaker LLM saw on this turn.

    Lets replay tools reproduce the exact prompt + payload Speaker received
    and verify the anti-leak invariants (no rubric / anchors / coverage in
    the payload). The ``speaker_input`` dict is the model_dump of
    ``SpeakerInput`` — kept loose-typed so adding a SpeakerInput field
    later doesn't require a schema migration here.
    """
    turn_id: str
    speaker_input: dict[str, Any]


class StateSnapshotPayload(BaseModel):
    """Audit payload capturing State Engine snapshots BEFORE process_judge_output.

    With this, replay tools can deterministically reconstruct any turn's
    inputs to the State Engine. The four fields are the model_dump of
    ``ledger_snapshot()``, ``queue_snapshot()``, ``claims_snapshot()``,
    ``lifecycle_snapshot()`` — kept loose-typed for the same reason as
    SpeakerInputPayload.
    """
    turn_id: str
    ledger: dict[str, Any]
    queue: dict[str, Any]
    claims: dict[str, Any]
    lifecycle: dict[str, Any]


class SpeakerOutputEmptyPayload(BaseModel):
    """Fired when the Speaker LLM streamed no audible text and the
    orchestrator played a deterministic fallback. Distinguished from
    SpeakerErrorPayload (which fires on an exception) and SpeakerCachedPayload
    (which fires on the deterministic repeat path).

    Phase 9.3 diagnostic fields (added 2026-05-10) capture WHY the
    Speaker came back empty so we can root-cause the issue without
    re-enabling verbose logging in production:

      * ``event_types_seen`` — every Responses-API event type we received
        on the stream. A normal turn includes ``response.created`` →
        ``response.output_item.added`` → many ``response.output_text.delta``
        → ``response.completed``. A SAFETY REFUSAL is the most common
        empty cause and shows ``response.refusal.delta`` + ``response.refusal.done``
        instead of any ``output_text.delta``.
      * ``refusal_text`` — content of any ``response.refusal.*`` deltas
        (joined). When non-empty this is the smoking gun for a content
        filter rejection.
      * ``response_id`` — OpenAI's request id for the call (if surfaced),
        usable to look up the trace upstream.
      * ``finish_reason`` — the response object's finish_reason if
        available (``stop`` / ``content_filter`` / ``length`` / etc.).
    """
    turn_id: str
    instruction_kind: str
    fallback_text: str
    event_types_seen: list[str] = Field(default_factory=list)
    refusal_text: str | None = None
    response_id: str | None = None
    finish_reason: str | None = None


class SpeakerErrorPayload(BaseModel):
    turn_id: str
    model: str
    error_class: str
    error_message: str = Field(max_length=500)
    recovery_utterance: str


class SpeakerInterruptedPayload(BaseModel):
    """Phase 9.4 (2026-05-10) — fired when the candidate interrupted the
    Speaker stream BEFORE any output text was produced. Distinct from:

      * ``speaker.output.empty`` — Speaker LLM produced nothing for a
        non-interruption reason (model decided "nothing to say", safety
        refusal, etc.). The orchestrator plays a deterministic fallback.
      * ``speaker.error`` — exception raised mid-stream. The orchestrator
        plays a canned recovery utterance.

    For ``speaker.interrupted`` the orchestrator does NOT play any
    fallback — the candidate is already speaking, so playing back would
    talk over them and create the death-spiral pattern observed in
    session f665498d (turns 14-18). The agent stays silent and the
    NEXT user turn drives the next reply.

    The diagnostic fields mirror the empty-output payload so the audit
    envelope makes the cancellation cause traceable.
    """
    turn_id: str
    instruction_kind: str
    event_types_seen: list[str] = Field(default_factory=list)
    response_id: str | None = None


# Lifecycle / checkpoint
class LifecycleTransitionPayload(BaseModel):
    turn_id: str | None
    from_state: str
    to_state: str


class CheckpointWrittenPayload(BaseModel):
    turn_id: str
    last_audit_seq_flushed: int = Field(ge=0)
    captured_at_ms: int = Field(ge=0)


# Frontend
class FrontendAttributePayload(BaseModel):
    turn_id: str | None
    attribute_name: str
    value: str


# Session terminal — fired when lifecycle is closing/closed and a candidate
# turn arrives. The orchestrator bypasses Judge entirely and plays a canned
# terminal message. This event records the attempt for forensic completeness.
class SessionTerminalDeliveredPayload(BaseModel):
    turn_id: str
    lifecycle_state: Literal["closing", "closed"]
    lifecycle_outcome: str | None  # last_outcome value if set
    message: str  # the canned terminal text actually delivered


# ---------------------------------------------------------------------------
# 2026-05-17 conversational-continuation payloads
#
# Six event kinds working together. The "turn" payloads record the
# orchestrator's continuation decisions; the "state.snapshot" payloads
# record the rollback machinery's progress through each turn. Together
# they let replay tools reconstruct exactly what happened on a turn that
# was aborted-and-stitched vs one that committed cleanly.
# ---------------------------------------------------------------------------


class TurnStitchedContinuationPayload(BaseModel):
    """Fired on the turn that re-processes a stitched utterance.

    The current turn's candidate_text was prepended with the prior
    aborted turn's text before the Judge call. Char counts are the
    redaction-safe summary; the actual text flows through the normal
    audit redaction path (metadata mode hashes; full mode preserves).
    """

    turn_id: str
    prior_chars: int = Field(ge=0)
    current_chars: int = Field(ge=0)
    combined_chars: int = Field(ge=0)
    # Monotonic ms between the prior aborted turn's TURN_ABORTED event
    # and the current turn's TURN_STARTED event. Useful for tuning the
    # endpointing max_delay against real session pause distributions.
    gap_ms: int = Field(ge=0)


class TurnAbortedForContinuationPayload(BaseModel):
    """Fired when the cancellation watcher fires before the commit point.

    ``phase`` records WHERE in the turn pipeline the abort happened:
    ``judge`` (most common — watcher fired during Judge LLM call),
    ``pre_speaker`` (Judge returned, State Engine processed, but Speaker
    not yet streaming), ``speaker_pre_commit`` (Speaker invoked but TTS
    audio hasn't first-played, i.e., we still hold the commit gate).

    ``elapsed_ms`` measures from ``on_user_turn_completed`` entry to
    the abort firing. ``consecutive_aborts`` is the post-increment value,
    used by replay tools to spot loop-guard triggers.
    """

    turn_id: str
    phase: Literal["judge", "pre_speaker", "speaker_pre_commit"]
    elapsed_ms: int = Field(ge=0)
    text_chars: int = Field(ge=0)
    consecutive_aborts: int = Field(ge=1)


class TurnLoopGuardFiredPayload(BaseModel):
    """Fired when consecutive aborts hit the cap and we force a commit.

    Skip-the-watcher signal — the current turn runs Judge → State →
    Speaker unmonitored to guarantee forward progress.
    """

    turn_id: str
    consecutive_aborts: int = Field(ge=3)


class StateSnapshotTakenPayload(BaseModel):
    """Pre-Judge snapshot captured. Pairs with the eventual
    STATE_SNAPSHOT_RESTORED (abort path) or STATE_SNAPSHOT_COMMITTED
    (success path) event.

    Carries lightweight forensic identifiers — full state lives in the
    in-process EngineCheckpoint, which is too big to persist per-turn.
    """

    turn_id: str
    transcript_entries: int = Field(ge=0)
    queue_active_index: int | None = None


class StateSnapshotRestoredPayload(BaseModel):
    """Pre-Speaker abort path completed: in-turn mutations have been
    rolled back via ``StateEngine.restore_from``.
    """

    turn_id: str


class StateSnapshotCommittedPayload(BaseModel):
    """Turn ran to its commit point; in-turn mutations are now permanent.
    Emitted on every successful turn for replay-symmetry with the abort
    path's restore event.
    """

    turn_id: str

"""Audit-envelope event-kind string constants.

Every value passed to ``EventCollector.append(kind=..., payload=...)``
in `app/modules/interview_engine/` is defined here so the extension
surface is auditable in one place. Drift in the kind strings would
silently break downstream indexing.

Convention:

* lowercase, dot-separated namespace (``<area>.<event>[.<sub>]``).
* Verb in past tense for actions (``rendered``, ``checked``,
  ``classified``); noun for state snapshots (``snapshot``).
* Areas: ``audio``, ``llm``, ``session``.

This file doubles as the seed for any future event-kind documentation:
every kind here would get a payload-shape spec written against it.

Post-cleanup (2026-05-06): the structured Phase A/B/C agent was removed
in favor of a generic LLM chatbot. The orchestrator / speech / evaluator
event kinds went with it. Only audio + session + llm kinds — the ones
the audio pipeline still emits — remain.
"""

# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

SESSION_CLOSE = "session.close"
SESSION_USAGE = "session.usage"
SESSION_TIMER_STARTED = "session.timer_started"


# ---------------------------------------------------------------------------
# Audio pipeline (LiveKit framework events)
# ---------------------------------------------------------------------------

AUDIO_USER_STATE = "audio.user.state"
AUDIO_AGENT_STATE = "audio.agent.state"
AUDIO_STT_TRANSCRIBED = "audio.stt.transcribed"
AUDIO_STT_KEYTERMS_APPLIED = "audio.stt.keyterms_applied"
AUDIO_INTERRUPTION_FALSE = "audio.interruption.false"
AUDIO_OVERLAP = "audio.overlap"
AUDIO_SPEECH_CREATED = "audio.speech.created"
AUDIO_PIPELINE_ERROR = "audio.pipeline.error"

# Note: `audio.metrics.<m.type>` is dynamically suffixed by the metric
# type (e.g. ``audio.metrics.llm``, ``audio.metrics.tts``). The prefix
# is fixed; suffixes are vendor-defined. Documented here as a prefix.
AUDIO_METRICS_PREFIX = "audio.metrics."

AUDIO_TUNING_SUMMARY = "audio.tuning_summary"


# ---------------------------------------------------------------------------
# LLM conversation (framework-driven STT → LLM → TTS loop)
# ---------------------------------------------------------------------------

LLM_MESSAGE_ADDED = "llm.message.added"
LLM_TOOL_EXECUTED = "llm.tool.executed"


# ---------------------------------------------------------------------------
# Engine turn loop (added 2026-05-07 for structured agent)
# ---------------------------------------------------------------------------

TURN_STARTED = "turn.started"
TURN_COMPLETED = "turn.completed"
TURN_COALESCED = "turn.coalesced"
# Stale-turn drop-and-drain (2026-05-11). Emitted when on_user_turn_completed
# receives a fragment whose stopped_speaking_at is past the configured
# staleness threshold AND a more-recent silence onset has been observed.
# The text is buffered for the next non-dropped turn instead of running
# Judge/Speaker on the stale input.
TURN_DROPPED = "turn.dropped"
# Emitted on the next non-dropped turn when one or more buffered stale
# texts are drained into ``candidate_text`` before the coalesce gate.
TURN_DRAIN_REPLAYED = "turn.drain_replayed"
JUDGE_CALL = "judge.call"
JUDGE_SYNTHETIC = "judge.synthetic"
JUDGE_FALLBACK = "judge.fallback"
JUDGE_VALIDATION = "judge.validation"
STATE_MUTATION = "state.mutation"
SPEAKER_CALL = "speaker.call"
SPEAKER_CACHED = "speaker.cached"
SPEAKER_OUTPUT = "speaker.output"
SPEAKER_INPUT = "speaker.input"
SPEAKER_OUTPUT_EMPTY = "speaker.output.empty"
SPEAKER_INTERRUPTED = "speaker.interrupted"
SPEAKER_ERROR = "speaker.error"
LIFECYCLE_TRANSITION = "lifecycle.transition"
STATE_SNAPSHOT = "state.snapshot"
CHECKPOINT_WRITTEN = "checkpoint.written"
FRONTEND_ATTRIBUTE_PUBLISHED = "frontend.attribute.published"
SESSION_TERMINAL_DELIVERED = "session.terminal_delivered"


# ---------------------------------------------------------------------------
# Conversational continuation (2026-05-17 design)
#
# When the candidate resumes speaking after a premature EOU, the
# orchestrator aborts the in-flight Judge call, restores the State
# Engine from a pre-turn snapshot, and stitches the prior text into the
# NEXT turn's input. The audit trail makes each step visible so replay
# tooling can reconstruct what happened.
# ---------------------------------------------------------------------------

# Emitted on the turn that re-processes a stitched utterance — its
# candidate_text was prepended with the prior aborted turn's text before
# the Judge call.
TURN_STITCHED_CONTINUATION = "turn.stitched_continuation"

# Emitted when the cancellation watcher fires and the turn aborts before
# the commit point (first TTS audio frame). The current turn's text is
# saved to _pending_continuation_text and StopResponse is raised.
TURN_ABORTED_FOR_CONTINUATION = "turn.aborted_for_continuation"

# Emitted when the 3-strike loop guard forces a commit. The current
# turn is processed without the cancellation watcher to guarantee
# forward progress for a candidate who keeps fragmenting.
TURN_LOOP_GUARD_FIRED = "turn.loop_guard_fired"

# Pre-Judge snapshot was captured. Forensic marker so replay tools can
# pair every commit/restore with its origin.
STATE_SNAPSHOT_TAKEN = "state.snapshot.taken"

# Snapshot was used to roll back in-turn mutations on cancellation. The
# next turn will see the State Engine as it was at snapshot time.
STATE_SNAPSHOT_RESTORED = "state.snapshot.restored"

# Turn ran to its commit point; in-turn mutations are now permanent.
# Emitted regardless of whether the watcher ever armed (e.g. when the
# loop guard skipped it).
STATE_SNAPSHOT_COMMITTED = "state.snapshot.committed"


# ---------------------------------------------------------------------------
# Aggregate registry — used by tests to assert no duplicates and by any
# future docs-generator to enumerate every kind. Adding a constant above
# without adding it here is a programmer error caught by
# ``tests/interview_engine/test_event_kinds.py``.
# ---------------------------------------------------------------------------

ALL_EVENT_KINDS: frozenset[str] = frozenset({
    SESSION_CLOSE,
    SESSION_USAGE,
    SESSION_TIMER_STARTED,
    AUDIO_USER_STATE,
    AUDIO_AGENT_STATE,
    AUDIO_STT_TRANSCRIBED,
    AUDIO_STT_KEYTERMS_APPLIED,
    AUDIO_INTERRUPTION_FALSE,
    AUDIO_OVERLAP,
    AUDIO_SPEECH_CREATED,
    AUDIO_PIPELINE_ERROR,
    AUDIO_TUNING_SUMMARY,
    LLM_MESSAGE_ADDED,
    LLM_TOOL_EXECUTED,
    TURN_STARTED,
    TURN_COMPLETED,
    TURN_COALESCED,
    TURN_DROPPED,
    TURN_DRAIN_REPLAYED,
    JUDGE_CALL,
    JUDGE_SYNTHETIC,
    JUDGE_FALLBACK,
    JUDGE_VALIDATION,
    STATE_MUTATION,
    SPEAKER_CALL,
    SPEAKER_CACHED,
    SPEAKER_OUTPUT,
    SPEAKER_INPUT,
    SPEAKER_OUTPUT_EMPTY,
    SPEAKER_INTERRUPTED,
    SPEAKER_ERROR,
    LIFECYCLE_TRANSITION,
    STATE_SNAPSHOT,
    CHECKPOINT_WRITTEN,
    FRONTEND_ATTRIBUTE_PUBLISHED,
    SESSION_TERMINAL_DELIVERED,
    TURN_STITCHED_CONTINUATION,
    TURN_ABORTED_FOR_CONTINUATION,
    TURN_LOOP_GUARD_FIRED,
    STATE_SNAPSHOT_TAKEN,
    STATE_SNAPSHOT_RESTORED,
    STATE_SNAPSHOT_COMMITTED,
})

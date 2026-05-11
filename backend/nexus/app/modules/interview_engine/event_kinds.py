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


# ---------------------------------------------------------------------------
# Audio pipeline (LiveKit framework events)
# ---------------------------------------------------------------------------

AUDIO_USER_STATE = "audio.user.state"
AUDIO_AGENT_STATE = "audio.agent.state"
AUDIO_STT_TRANSCRIBED = "audio.stt.transcribed"
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
JUDGE_CALL = "judge.call"
JUDGE_SYNTHETIC = "judge.synthetic"
JUDGE_FALLBACK = "judge.fallback"
JUDGE_VALIDATION = "judge.validation"
STATE_MUTATION = "state.mutation"
SPEAKER_CALL = "speaker.call"
SPEAKER_CACHED = "speaker.cached"
SPEAKER_OUTPUT = "speaker.output"
SPEAKER_OUTPUT_EMPTY = "speaker.output.empty"
SPEAKER_INTERRUPTED = "speaker.interrupted"
SPEAKER_OPENER_PLAYED = "speaker.opener.played"
SPEAKER_ERROR = "speaker.error"
LIFECYCLE_TRANSITION = "lifecycle.transition"
CHECKPOINT_WRITTEN = "checkpoint.written"
FRONTEND_ATTRIBUTE_PUBLISHED = "frontend.attribute.published"
SESSION_TERMINAL_DELIVERED = "session.terminal_delivered"


# ---------------------------------------------------------------------------
# Aggregate registry — used by tests to assert no duplicates and by any
# future docs-generator to enumerate every kind. Adding a constant above
# without adding it here is a programmer error caught by
# ``tests/interview_engine/test_event_kinds.py``.
# ---------------------------------------------------------------------------

ALL_EVENT_KINDS: frozenset[str] = frozenset({
    SESSION_CLOSE,
    SESSION_USAGE,
    AUDIO_USER_STATE,
    AUDIO_AGENT_STATE,
    AUDIO_STT_TRANSCRIBED,
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
    JUDGE_CALL,
    JUDGE_SYNTHETIC,
    JUDGE_FALLBACK,
    JUDGE_VALIDATION,
    STATE_MUTATION,
    SPEAKER_CALL,
    SPEAKER_CACHED,
    SPEAKER_OUTPUT,
    SPEAKER_OUTPUT_EMPTY,
    SPEAKER_INTERRUPTED,
    SPEAKER_OPENER_PLAYED,
    SPEAKER_ERROR,
    LIFECYCLE_TRANSITION,
    CHECKPOINT_WRITTEN,
    FRONTEND_ATTRIBUTE_PUBLISHED,
    SESSION_TERMINAL_DELIVERED,
})

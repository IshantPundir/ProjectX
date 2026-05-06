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


# ---------------------------------------------------------------------------
# LLM conversation (framework-driven STT → LLM → TTS loop)
# ---------------------------------------------------------------------------

LLM_MESSAGE_ADDED = "llm.message.added"
LLM_TOOL_EXECUTED = "llm.tool.executed"


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
    LLM_MESSAGE_ADDED,
    LLM_TOOL_EXECUTED,
})

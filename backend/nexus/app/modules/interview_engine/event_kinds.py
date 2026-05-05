"""Audit-envelope event-kind string constants.

Every value passed to ``EventCollector.append(kind=..., payload=...)``
in `app/modules/interview_engine/` should be defined here so the
extension surface is auditable in one place. The Report Builder
(Phase 3D, downstream) reads the audit envelope and indexes payloads
by ``kind``; drift in the kind strings would silently break its
indexing.

Convention:

* lowercase, dot-separated namespace (``<area>.<event>[.<sub>]``).
* Verb in past tense for actions (``rendered``, ``checked``,
  ``classified``); noun for state snapshots (``snapshot``).
* Areas: ``audio``, ``llm``, ``session``, ``orchestrator``, ``speech``,
  ``evaluator``, ``persistence``, ``system``.

This file doubles as the seed for the
``docs/interview_engine/event_kinds.md`` Phase J deliverable: every
kind here will get a payload-shape spec written against it. Adding a
kind here is the trigger to also document its payload there.

Kinds are grouped by phase that introduces them. The structured agent
build is phased (A through J) — the Phase B+ kinds are NOT emitted
yet; they're declared up-front so that future phases can ``import``
the constants instead of typing magic strings.
"""

# ---------------------------------------------------------------------------
# Existing kinds — emitted today by `agent.py` (the clean-slate generic
# harness). The structured agent (Phase B) keeps these unchanged.
# ---------------------------------------------------------------------------

SESSION_CLOSE = "session.close"
SESSION_USAGE = "session.usage"

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

LLM_MESSAGE_ADDED = "llm.message.added"
LLM_TOOL_EXECUTED = "llm.tool.executed"


# ---------------------------------------------------------------------------
# Phase B — Orchestrator skeleton (state-machine driving deterministic flow).
# ---------------------------------------------------------------------------

ORCHESTRATOR_PHASE_CHANGED = "orchestrator.phase_changed"
ORCHESTRATOR_QUESTION_ASKED = "orchestrator.question_asked"
ORCHESTRATOR_QUESTION_COMPLETED = "orchestrator.question_completed"
ORCHESTRATOR_FOLLOWUP_ASKED = "orchestrator.followup_asked"
ORCHESTRATOR_EXIT = "orchestrator.exit"


# ---------------------------------------------------------------------------
# Phase C — Speech Agent (LLM-generated utterances via session.say).
# ---------------------------------------------------------------------------

SPEECH_RENDERED = "speech.rendered"
SPEECH_FALLBACK_USED = "speech.fallback_used"
SPEECH_STREAM_INTERRUPTED = "speech.stream_interrupted"


# ---------------------------------------------------------------------------
# Phase D / E — Sufficiency Checker.
# ---------------------------------------------------------------------------

EVALUATOR_SUFFICIENCY_CHECKED = "evaluator.sufficiency.checked"


# ---------------------------------------------------------------------------
# Phase F — Intent Classifier.
# ---------------------------------------------------------------------------

EVALUATOR_INTENT_CLASSIFIED = "evaluator.intent.classified"


# ---------------------------------------------------------------------------
# Phase G — Deepening probes.
# ---------------------------------------------------------------------------

ORCHESTRATOR_DEEPENING_PROBE = "orchestrator.deepening_probe"


# ---------------------------------------------------------------------------
# Phase H — Disclaim Classifier + knockout flow.
# ---------------------------------------------------------------------------

EVALUATOR_DISCLAIM_CHECKED = "evaluator.disclaim.checked"
ORCHESTRATOR_KNOCKOUT_CONFIRMATION_ENTERED = (
    "orchestrator.knockout_confirmation_entered"
)
ORCHESTRATOR_KNOCKOUT_CONFIRMED = "orchestrator.knockout_confirmed"
ORCHESTRATOR_KNOCKOUT_CORRECTED = "orchestrator.knockout_corrected"
ORCHESTRATOR_KNOCKOUT_AMBIGUOUS_CONTINUE = (
    "orchestrator.knockout_ambiguous_continue"
)
ORCHESTRATOR_KNOCKOUT_FAILURE_RECORDED = "orchestrator.knockout_failure_recorded"


# ---------------------------------------------------------------------------
# Phase I — Silence policy, reconnect, pause request.
# ---------------------------------------------------------------------------

ORCHESTRATOR_SILENCE_TIER = "orchestrator.silence_tier"
ORCHESTRATOR_RECONNECT = "orchestrator.reconnect"
ORCHESTRATOR_CANDIDATE_INITIATED_EXIT_CHOICE = (
    "orchestrator.candidate_initiated_exit_choice"
)


# ---------------------------------------------------------------------------
# Phase J — Hardening + final ledger snapshot + version provenance.
# ---------------------------------------------------------------------------

ORCHESTRATOR_LEDGER_SNAPSHOT = "orchestrator.ledger.snapshot"
SYSTEM_VERSIONS = "system.versions"


# ---------------------------------------------------------------------------
# Persistence layer (A.4) — Redis writeback observability events.
# Logged at structlog level today; declared here so future migration to
# the audit envelope (if useful) has a stable kind.
# ---------------------------------------------------------------------------

PERSISTENCE_GAPS_DETECTED = "persistence.gaps_detected"


# ---------------------------------------------------------------------------
# Aggregate registry — used by tests to assert no duplicates and by the
# Phase J docs-generator to enumerate every kind. Adding a constant
# above without adding it here is a programmer error caught by
# `tests/interview_engine/test_event_kinds.py`.
# ---------------------------------------------------------------------------

ALL_EVENT_KINDS: frozenset[str] = frozenset({
    # existing
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
    # Phase B
    ORCHESTRATOR_PHASE_CHANGED,
    ORCHESTRATOR_QUESTION_ASKED,
    ORCHESTRATOR_QUESTION_COMPLETED,
    ORCHESTRATOR_FOLLOWUP_ASKED,
    ORCHESTRATOR_EXIT,
    # Phase C
    SPEECH_RENDERED,
    SPEECH_FALLBACK_USED,
    SPEECH_STREAM_INTERRUPTED,
    # Phase D / E
    EVALUATOR_SUFFICIENCY_CHECKED,
    # Phase F
    EVALUATOR_INTENT_CLASSIFIED,
    # Phase G
    ORCHESTRATOR_DEEPENING_PROBE,
    # Phase H
    EVALUATOR_DISCLAIM_CHECKED,
    ORCHESTRATOR_KNOCKOUT_CONFIRMATION_ENTERED,
    ORCHESTRATOR_KNOCKOUT_CONFIRMED,
    ORCHESTRATOR_KNOCKOUT_CORRECTED,
    ORCHESTRATOR_KNOCKOUT_AMBIGUOUS_CONTINUE,
    ORCHESTRATOR_KNOCKOUT_FAILURE_RECORDED,
    # Phase I
    ORCHESTRATOR_SILENCE_TIER,
    ORCHESTRATOR_RECONNECT,
    ORCHESTRATOR_CANDIDATE_INITIATED_EXIT_CHOICE,
    # Phase J
    ORCHESTRATOR_LEDGER_SNAPSHOT,
    SYSTEM_VERSIONS,
    # Persistence
    PERSISTENCE_GAPS_DETECTED,
})

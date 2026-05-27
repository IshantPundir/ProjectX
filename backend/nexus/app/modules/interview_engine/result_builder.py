"""Pure assembly of a v2 SessionResult from the session's coverage + envelope + transcript.

v2 reuses interview_runtime.record_session_result (CMI-1): it populates coverage_summary
(v2-native) and leaves the v1 snapshot fields (signal_ledger/question_queue/claims_pool)
None. Counts are derived from the audit envelope's directive.delivered events
(engine-agnostic). No livekit, no IO, no LLM.
"""
from __future__ import annotations

from app.modules.interview_engine.coverage import CoverageTracker
from app.modules.interview_engine.event_log.envelope import EventLogEnvelope
from app.modules.interview_runtime import (
    KnockoutFailure,
    SessionConfig,
    SessionResult,
    TranscriptEntry,
)


def _delivered_acts(envelope: EventLogEnvelope) -> list[str]:
    return [e.payload.get("act", "") for e in envelope.events if e.kind == "directive.delivered"]


def build_v2_session_result(
    *,
    config: SessionConfig,
    coverage: CoverageTracker,
    transcript: list[TranscriptEntry],
    envelope: EventLogEnvelope,
    audio_summary: dict[str, object] | None,
    knockout_failures: list[KnockoutFailure],
    duration_seconds: float,
    completed_at: str,
    audit_envelope_ref: str | None,
) -> SessionResult:
    """Assemble a v2 SessionResult from pure, in-memory inputs.

    Counts semantics:
    - questions_asked   = delivered ASK + ACK_ADVANCE (each introduces a new bank question)
    - total_probes_fired = delivered PROBE
    - questions_skipped = max(0, len(bank) - questions_asked)

    v1 snapshot fields (signal_ledger / question_queue / claims_pool) are left None.
    coverage_summary is populated from the CoverageTracker (v2-native).
    """
    acts = _delivered_acts(envelope)
    questions_asked = sum(1 for a in acts if a in ("ASK", "ACK_ADVANCE"))
    total_probes_fired = sum(1 for a in acts if a == "PROBE")
    questions_skipped = max(0, len(config.stage.questions) - questions_asked)
    return SessionResult(
        session_id=config.session_id,
        job_title=config.job_title,
        stage_id=config.stage.stage_id,
        stage_type=config.stage.stage_type,
        candidate_name=config.candidate.name,
        duration_seconds=duration_seconds,
        questions_asked=questions_asked,
        questions_skipped=questions_skipped,
        total_probes_fired=total_probes_fired,
        full_transcript=transcript,
        completed_at=completed_at,
        knockout_failures=knockout_failures,
        audio_tuning_summary=audio_summary,
        coverage_summary=coverage.summary_for_result(),   # v2-native
        audit_envelope_ref=audit_envelope_ref,
        # signal_ledger / question_queue / claims_pool stay at their None defaults (v1-only)
    )

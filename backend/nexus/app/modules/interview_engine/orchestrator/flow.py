"""Pure orchestration-flow functions for Phase B linear progression.

No I/O, no LLMs, no DB. The structured agent (Phase B) calls these on
every candidate-turn-resolution boundary to decide what to do next.

Phase B scope:
* `pick_next_question` walks ``config.stage.questions`` in the order the
  upstream provider produced — ``build_session_config`` orders by
  ``is_mandatory DESC, position ASC`` (mandatory-first, then
  position-ascending within each tier).
* `evaluate_exit_condition` returns `ExitMode.COMPLETED` exactly when
  `pick_next_question` is None; otherwise returns None.

Phase E reintroduces a richer `pick_next_question` (priority + mandatory-
first + knockout-first within mandatory) once the SignalLedger drives
selection. The Phase-B implementation here remains valid as the no-
signal-data fallback.
"""
from __future__ import annotations

from app.modules.interview_engine.orchestrator.state import (
    ExitMode,
    InterviewState,
)
from app.modules.interview_runtime import QuestionConfig, SessionConfig


def pick_next_question(
    state: InterviewState,
    config: SessionConfig,
) -> QuestionConfig | None:
    """Return the next QuestionConfig to ask, or None if all are done.

    Phase B: walks ``config.stage.questions`` in the order the upstream
    provider produced — ``build_session_config`` orders by
    ``is_mandatory DESC, position ASC`` (mandatory-first, then
    position-ascending within each tier). A question is considered "done"
    when there is a corresponding ``QuestionState`` in ``state.questions``
    with a non-None ``completed_at``. The first question with no
    QuestionState (not yet asked) — or with QuestionState but
    ``completed_at is None`` (in progress) — is returned.

    Returns None when every question has a QuestionState with
    completed_at set, OR when ``config.stage.questions`` is empty.
    """
    completed_ids: set[str] = {
        qs.question_id
        for qs in state.questions
        if qs.completed_at is not None
    }
    for qc in config.stage.questions:
        if qc.id not in completed_ids:
            return qc
    return None


def evaluate_exit_condition(
    state: InterviewState,
    config: SessionConfig,
) -> ExitMode | None:
    """Return ExitMode.COMPLETED iff every question is done; else None.

    Phase B: COMPLETED is the only exit condition this function detects.
    The orchestrator handles disconnect (TECHNICAL_FAILURE) and
    candidate-initiated exit (CANDIDATE_INITIATED_EXIT) paths
    separately because those are driven by external events, not flow
    state. Phase H/I will add more conditions here as those paths land.
    """
    if pick_next_question(state, config) is None:
        return ExitMode.COMPLETED
    return None


__all__ = ["evaluate_exit_condition", "pick_next_question"]

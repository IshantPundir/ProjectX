"""Deterministic replay of session 8317142f-3166-4236-a43c-18c8ab4592e1.

The recorded audit envelope captured the exact Judge inputs and outputs
that produced bugs A, B, and C. Replaying the recorded JudgeOutputs
through a fresh StateEngine asserts the new guards do their job.

Pure Python — no LLM calls, no LiveKit, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.interview_engine.models.judge import JudgeOutput
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.engine import StateEngine
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SignalMetadata,
    StageConfig,
)


ENVELOPE_PATH = (
    Path(__file__).parents[2]
    / "engine-events"
    / "8317142f-3166-4236-a43c-18c8ab4592e1.json"
)

# The single signal value that the recorded session targeted. Pulled
# directly from the envelope's judge.call observations so the replay's
# SignalLedger accepts the recorded observations verbatim.
JIRA_SIGNAL = (
    "Configure and manage Jira projects and workflow components "
    "(issue types, workflows, screens, fields, schemes, automation rules) "
    "aligned to business processes"
)

# The recorded session was built with 5 questions; the LiveKit attribute
# events (`total_questions=5`) confirm this. We don't have the original
# question texts on disk, so reconstruct minimal-but-valid question
# configs that satisfy the schema. For Bug C / Bug B replay it only
# matters that:
#   - position 0 exists with a recognizable question text (used by the
#     repeat-cache assertion);
#   - all questions are tied to JIRA_SIGNAL so the recorded observations
#     route through the active question.
_RECORDED_QUESTION_IDS = ["q0", "q1", "q2", "q3", "q4"]
_FIRST_QUESTION_TEXT = (
    "Walk me through how you’d design or refactor a Jira project "
    "to fit a client workflow."
)


def _build_question(qid: str, position: int, text: str) -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=position,
        text=text,
        signal_values=[JIRA_SIGNAL],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=[
            "Walk me through one concrete rule selection tradeoff.",
            "How do you choose automation tooling for workflow logic?",
            "How do you validate a workflow change before rollout?",
        ],
        positive_evidence=[
            "issue-types-anchor",
            "workflow-anchor",
            "validators-anchor",
        ],
        red_flags=["no-experience-flag", "blocker-flag"],
        rubric=QuestionRubric(
            excellent="ex" * 5, meets_bar="mb" * 5, below_bar="bb" * 5,
        ),
        evaluation_hint="hint hint hint hint hint",
        question_kind="technical_depth",
    )


def _build_session_config_from_envelope(envelope: dict) -> SessionConfig:
    """Reconstruct a minimal SessionConfig that mirrors the recorded session.

    The envelope itself does not embed the original question/signal config
    (those came from `build_session_config` at session start, which is
    elsewhere). We rebuild the smallest scaffold that lets the recorded
    JudgeOutput payloads validate cleanly through the StateEngine:
      - 5 questions (matches `total_questions` attribute event).
      - Single signal `JIRA_SIGNAL` (matches every observation in turn 7).
      - knockout=True on that signal so the ->failed-with-anchor-0 guard
        is actually load-bearing — without knockout=True the test would
        pass trivially (no policy override path to suppress).
      - duration_minutes=15 (matches the initial `time_remaining_seconds=900`
        attribute event).
    """
    questions = [
        _build_question(qid, idx, _FIRST_QUESTION_TEXT if idx == 0 else f"Question {idx}.")
        for idx, qid in enumerate(_RECORDED_QUESTION_IDS)
    ]
    return SessionConfig(
        session_id=envelope["session_id"],
        job_id="job-replay-8317142f",
        candidate_id="cand-replay-8317142f",
        job_title="Atlassian Jira Administrator",
        role_summary="Configure Jira projects and workflows for client teams.",
        seniority_level="Mid-Senior",
        company=CompanyContext(
            about="Replay tenant for the failing-session audit envelope.",
            industry="software",
            company_stage="growth",
            hiring_bar="High bar — Jira admin depth required.",
        ),
        candidate=CandidateContext(name="Ishant"),
        stage=StageConfig(
            stage_id="stg-replay",
            name="AI Screening",
            stage_type="ai_screening",
            difficulty="medium",
            duration_minutes=15,
            questions=questions,
        ),
        signals=[JIRA_SIGNAL],
        signal_metadata=[
            SignalMetadata(
                value=JIRA_SIGNAL,
                type="competency",
                priority="required",
                weight=3,
                knockout=True,
                stage="screen",
                evaluation_method="verbal_response",
            ),
        ],
    )


def _judge_calls(envelope: dict) -> list[dict]:
    return [e for e in envelope["events"] if e["kind"] == "judge.call"]


def _speaker_calls(envelope: dict) -> list[dict]:
    return [e for e in envelope["events"] if e["kind"] == "speaker.call"]


# Map legacy speaker instruction kinds (from the recorded envelope) onto
# the current InstructionKind enum. The recorded session predates the
# `redirect_*` collapse (Task 9): every recorded `redirect_off_topic`
# now corresponds to the unified `redirect` kind. The guard cache
# behavior we are testing only depends on whether the kind belongs to
# `_QUESTION_KINDS`, which neither the legacy nor current redirect
# variants do.
_LEGACY_INSTRUCTION_REMAP = {
    "redirect_off_topic": InstructionKind.redirect,
    "redirect_unclear": InstructionKind.redirect,
    "redirect_clarify_request": InstructionKind.redirect,
}


def _resolve_instruction_kind(raw: str) -> InstructionKind:
    if raw in _LEGACY_INSTRUCTION_REMAP:
        return _LEGACY_INSTRUCTION_REMAP[raw]
    return InstructionKind(raw)


@pytest.fixture(scope="module")
def envelope() -> dict:
    return json.loads(ENVELOPE_PATH.read_text())


# --- Sanity ---


def test_failing_session_envelope_loadable(envelope):
    """The envelope loads and contains the expected structure."""
    assert envelope["session_id"] == "8317142f-3166-4236-a43c-18c8ab4592e1"
    calls = _judge_calls(envelope)
    assert len(calls) == 7  # 7 judge calls in the recorded session


# --- Bug C replay ---


def test_turn_7_failed_with_anchor_zero_is_dropped(envelope):
    """Turn 7's bogus sufficient->failed observation (anchor_id=0) must
    be dropped by the State Engine guard. Verifies Bug C fix.

    The recorded observation list for turn 7 contains four entries:
      0. none→partial,         anchor_id=0  (legitimate)
      1. partial→partial,      anchor_id=0  (legitimate)
      2. partial→sufficient,   anchor_id=0  (legitimate)
      3. sufficient→failed,    anchor_id=0  (BUG — failure with positive anchor)

    The State Engine's `→failed semantic guard` (Task 4) must drop
    observation #3 *before* knockout detection runs, so:
      - lifecycle.knockout_failures stays empty
      - lifecycle stays `active`
      - the Judge's intended action (probe) survives
      - an `illegal_failure_observation` warning is recorded
    """
    calls = _judge_calls(envelope)
    turn7 = calls[6]  # 0-indexed, 7th judge call
    judge_output_data = turn7["payload"]["output"]
    judge_output = JudgeOutput.model_validate(judge_output_data)

    # The recorded payload has 4 observations; assert the bogus shape
    # before exercising the engine so any envelope drift surfaces here.
    assert len(judge_output.observations) == 4
    bogus_obs = judge_output.observations[3]
    assert bogus_obs.anchor_id == 0
    assert bogus_obs.coverage_transition.value == "sufficient→failed"

    # Run through a fresh State Engine.
    cfg = _build_session_config_from_envelope(envelope)
    engine = StateEngine(session_config=cfg)
    engine.process_judge_output(
        turn_id="t0",
        judge_output=engine.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )
    decision = engine.process_judge_output(
        turn_id="t1",
        judge_output=judge_output,
        candidate_utterance_text=(
            "Sure. So first time would understand the client's "
            "workflow and map that into Jira work."
        ),
        elapsed_ms=140_000,
    )

    # No knockout fired (the bogus ->failed observation was dropped).
    assert engine.lifecycle_snapshot().knockout_failures == []
    # Lifecycle remains active (no policy override path was taken).
    assert engine.lifecycle_snapshot().state.value == "active"
    # The Judge's intended action (probe) survived end-to-end.
    assert decision.speaker_input.instruction_kind == InstructionKind.deliver_probe
    # The illegal_failure_observation warning is recorded for audit.
    codes = [w.code for w in decision.validation_warnings]
    assert "illegal_failure_observation" in codes


# --- Bug B replay ---


def test_turn_5_repeat_replays_question_not_redirect(envelope):
    """Turn 5's repeat should replay the cached QUESTION utterance from
    turn 0, not the redirect from turn 4. Verifies Bug B fix
    (Task 5: repeat-cache filter).

    Walks the recorded speaker.call events and registers the first 4
    utterances on a fresh State Engine — turn 0 is
    `deliver_first_question` (cached as a question), turns 1-3 are
    clarify / redirect_off_topic (NOT cached). Then exercises
    `_resolve_repeat`: it must return turn 0's question text, not the
    later redirect text.
    """
    cfg = _build_session_config_from_envelope(envelope)
    engine = StateEngine(session_config=cfg)
    engine.process_judge_output(
        turn_id="t0",
        judge_output=engine.initialize_for_session_start(),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )

    # Replay the first 4 agent utterances from the envelope, mapped to
    # current InstructionKind values. The first one is the original
    # question; the rest are clarifies/redirects.
    speaker_calls = _speaker_calls(envelope)[:4]
    assert speaker_calls, "envelope missing speaker.call events"
    assert speaker_calls[0]["payload"]["instruction_kind"] == "deliver_first_question"

    first_question_utterance: str | None = None
    redirect_utterance_seen: str | None = None
    for sc in speaker_calls:
        payload = sc["payload"]
        text = payload.get("final_utterance") or ""
        if not text:
            # Some speaker.call events recorded an empty final_utterance
            # (Bug D). Skip them — they can't be replayed.
            continue
        kind = _resolve_instruction_kind(payload["instruction_kind"])
        engine.register_agent_utterance(
            turn_id=payload["turn_id"], text=text, instruction_kind=kind,
        )
        if kind == InstructionKind.deliver_first_question and first_question_utterance is None:
            first_question_utterance = text
        if kind == InstructionKind.redirect:
            redirect_utterance_seen = text

    assert first_question_utterance is not None, (
        "envelope must contain at least one deliver_first_question utterance"
    )
    assert redirect_utterance_seen is not None, (
        "envelope must contain at least one redirect utterance for this test "
        "to be load-bearing"
    )

    # Now resolve `repeat`. The cache must return the original question,
    # NOT the more-recent redirect.
    instruction, cached, _source_turn = engine._resolve_repeat(warnings=[])
    assert instruction == InstructionKind.repeat
    assert cached == first_question_utterance
    assert cached != redirect_utterance_seen
    # Sanity: the cached text really is the question, not the redirect.
    assert "Walk me through" in cached
    assert "stay on the Jira workflow side" not in cached

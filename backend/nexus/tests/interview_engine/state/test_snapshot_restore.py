"""Tests for StateEngine.snapshot_full() and restore_from().

Required by the 2026-05-17 conversational-continuation design. The
orchestrator takes a snapshot at the top of on_user_turn_completed,
runs Judge → State Engine → Speaker against the live engine, and on
abort calls restore_from to wipe the in-turn mutations cleanly.

A round-trip must preserve EVERY field that process_judge_output
mutates: ledger entries + per-signal snapshots, queue (active index,
push-back counts, probes consumed, quality observations, turn counters),
claims pool, lifecycle (state, time elapsed, last_outcome,
knockout_failures), turn_count, transcript, question_utterances.
"""
from __future__ import annotations

from app.modules.interview_engine.models.judge import (
    AdvancePayload, CoverageQuality, CoverageTransition,
    JudgeOutput, NextAction, Observation, ProbePayload, TurnMetadata,
)
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.state.checkpoint import EngineCheckpoint
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_engine.state.lifecycle import LifecycleState


def _build_engine(make_session_config, make_question, knockout: str | None = None) -> StateEngine:
    """Engine with two mandatory questions and one optional, signals S1/S2."""
    cfg = make_session_config(
        questions=[
            make_question(qid="q1", position=0, mandatory=True, signal_values=["S1"], follow_ups=["follow1"]),
            make_question(qid="q2", position=1, mandatory=True, signal_values=["S2"]),
            make_question(qid="q3", position=2, mandatory=False, signal_values=["S1"]),
        ],
        signals=["S1", "S2"],
        knockout_signal=knockout,
    )
    return StateEngine(session_config=cfg, config=StateEngineConfig(claims_pool_max=10))


def _advance_to_q1(engine: StateEngine, make_judge_output) -> None:
    """Drive the engine through its synthetic start so q1 is active."""
    engine.process_judge_output(
        turn_id="t-syn",
        judge_output=make_judge_output(action=NextAction.advance, target="q1"),
        candidate_utterance_text=None,
        elapsed_ms=0,
    )


def test_snapshot_full_returns_engine_checkpoint_v2(make_session_config, make_question, make_judge_output):
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)

    snap = engine.snapshot_full()
    assert isinstance(snap, EngineCheckpoint)
    assert snap.schema_version == 2
    assert snap.session_id == "sess-test"
    # All four sub-snapshots must be populated.
    assert snap.queue.active_index == 0  # q1
    # No observations recorded yet → ledger snapshots exist but with coverage=none.
    assert "S1" in snap.ledger.snapshots
    # Turn count was incremented by the synthetic start.
    assert snap.turn_count == 1


def test_restore_from_rewinds_ledger_observation(make_session_config, make_question, make_judge_output):
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)

    pre_snap = engine.snapshot_full()

    # Apply a probe + concrete observation on S1 — mutates ledger AND queue.
    obs = Observation(
        signal_value="S1", anchor_id=0, evidence_quote="I built this last quarter.",
        coverage_transition=CoverageTransition.none_to_partial,
        quality=CoverageQuality.concrete,
    )
    engine.process_judge_output(
        turn_id="t-probe",
        judge_output=JudgeOutput(
            observations=[obs],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=ProbePayload(probe_id="0"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="I worked on it directly.",
        elapsed_ms=5000,
    )
    # Verify the mutation happened.
    assert engine.ledger_snapshot().snapshots["S1"].coverage.value == "partial"
    assert engine.turn_count_snapshot() == 2
    assert len(engine.transcript_snapshot()) == 1

    # Restore.
    engine.restore_from(pre_snap)

    # Mutation gone.
    assert engine.ledger_snapshot().snapshots["S1"].coverage.value == "none"
    assert engine.turn_count_snapshot() == 1
    assert engine.transcript_snapshot() == []


def test_restore_from_rewinds_queue_advance(make_session_config, make_question, make_judge_output):
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)
    pre_snap = engine.snapshot_full()

    # Force-advance through Judge advance to q2.
    obs_concrete = Observation(
        signal_value="S1", anchor_id=0, evidence_quote="ok",
        coverage_transition=CoverageTransition.none_to_partial,
        quality=CoverageQuality.concrete,
    )
    engine.process_judge_output(
        turn_id="t-adv",
        judge_output=JudgeOutput(
            observations=[obs_concrete],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q2"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="moving on",
        elapsed_ms=1000,
    )
    assert engine.queue_snapshot().active_index == 1  # q2

    engine.restore_from(pre_snap)
    assert engine.queue_snapshot().active_index == 0  # back to q1


def test_restore_from_rewinds_question_utterances(make_session_config, make_question, make_judge_output):
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)
    pre_snap = engine.snapshot_full()

    engine.register_agent_question_for_repeat(
        turn_id="t-1", text="What's your strongest signal?",
        instruction_kind=InstructionKind.deliver_question,
    )
    assert "t-1" in engine._question_utterances

    engine.restore_from(pre_snap)
    assert "t-1" not in engine._question_utterances


def test_restore_from_rewinds_lifecycle_state(make_session_config, make_question, make_judge_output):
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)
    pre_snap = engine.snapshot_full()
    assert engine.lifecycle_snapshot().state == LifecycleState.active

    # Trigger a polite_close which transitions lifecycle to closing.
    engine.process_judge_output(
        turn_id="t-close",
        judge_output=JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.polite_close,
            next_action_payload={"kind": "polite_close"},
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text=None,
        elapsed_ms=2000,
    )
    assert engine.lifecycle_snapshot().state == LifecycleState.closing

    engine.restore_from(pre_snap)
    assert engine.lifecycle_snapshot().state == LifecycleState.active


def test_snapshot_full_then_restore_is_full_round_trip(make_session_config, make_question, make_judge_output):
    """Composite mutation sequence; restore = byte-identical pre-state."""
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)

    # Do a handful of mutations of different kinds.
    obs = Observation(
        signal_value="S1", anchor_id=0, evidence_quote="solid example",
        coverage_transition=CoverageTransition.none_to_partial,
        quality=CoverageQuality.concrete,
    )
    engine.process_judge_output(
        turn_id="t-1",
        judge_output=JudgeOutput(
            observations=[obs],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=ProbePayload(probe_id="0"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="answer one",
        elapsed_ms=500,
    )
    engine.register_agent_utterance(
        turn_id="t-1", text="Tell me more.",
        instruction_kind=InstructionKind.deliver_probe,
    )

    snap = engine.snapshot_full()

    # More mutations after snapshot.
    obs2 = Observation(
        signal_value="S2", anchor_id=1, evidence_quote="another good example",
        coverage_transition=CoverageTransition.none_to_partial,
        quality=CoverageQuality.strong,
    )
    engine.process_judge_output(
        turn_id="t-2",
        judge_output=JudgeOutput(
            observations=[obs2],
            candidate_claims=[],
            next_action=NextAction.advance,
            next_action_payload=AdvancePayload(target_question_id="q2"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="answer two",
        elapsed_ms=1500,
    )

    # Restore should erase t-2.
    engine.restore_from(snap)

    # Verify by comparing all snapshots are equal to snap's snapshots.
    assert engine.ledger_snapshot() == snap.ledger
    assert engine.queue_snapshot() == snap.queue
    assert engine.claims_snapshot() == snap.claims
    assert engine.lifecycle_snapshot() == snap.lifecycle
    assert engine.turn_count_snapshot() == snap.turn_count
    assert engine.transcript_snapshot() == snap.transcript
    assert engine._question_utterances == snap.question_utterances


def test_snapshot_does_not_alias_internal_state(make_session_config, make_question, make_judge_output):
    """Mutations after snapshot must not bleed back into the snapshot."""
    engine = _build_engine(make_session_config, make_question)
    _advance_to_q1(engine, make_judge_output)

    snap = engine.snapshot_full()
    transcript_len_pre = len(snap.transcript)

    # Append to live transcript via process_judge_output.
    engine.process_judge_output(
        turn_id="t-after",
        judge_output=JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=ProbePayload(probe_id="0"),
            turn_metadata=TurnMetadata(),
        ),
        candidate_utterance_text="post-snapshot speech",
        elapsed_ms=10000,
    )

    # snap.transcript must NOT reflect the mutation.
    assert len(snap.transcript) == transcript_len_pre

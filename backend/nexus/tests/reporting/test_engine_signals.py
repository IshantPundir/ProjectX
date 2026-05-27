from app.modules.reporting.scoring.engine_signals import (
    build_engine_states, detect_knockout_close, collect_signal_evidence,
)
from app.modules.reporting.scoring.types import SignalDef

SIGS = [
    SignalDef("A", "competency", 3, knockout=False, priority="required"),
    SignalDef("B", "experience", 3, knockout=True, priority="required"),
]


def test_build_engine_states_defaults_unknown_to_none():
    states = build_engine_states({"A": "partial"}, SIGS)
    assert states == {"A": "partial", "B": "none"}


def test_build_engine_states_ignores_signals_not_in_metadata():
    states = build_engine_states({"A": "sufficient", "ZZ": "failed"}, SIGS)
    assert "ZZ" not in states


def test_detect_knockout_close_returns_trigger():
    env = {"events": [
        {"kind": "turn.decision", "payload": {
            "move": "advance", "attributed_signals": ["B"],
            "coverage_delta": {"B": "failed"}, "candidate_quote": "never did it"}},
        {"kind": "turn.decision", "payload": {
            "move": "knockout_close", "attributed_signals": [],
            "coverage_delta": {}, "candidate_quote": "hello?"}},
    ]}
    ko = detect_knockout_close(env)
    assert ko is not None
    assert ko.signal == "B"      # most-recent failed signal before the close
    assert ko.reason


def test_detect_knockout_close_none_when_no_close():
    env = {"events": [{"kind": "turn.decision", "payload": {"move": "advance"}}]}
    assert detect_knockout_close(env) is None


def test_collect_signal_evidence_gathers_touching_turns():
    env = {"events": [
        {"kind": "turn.decision", "payload": {
            "attributed_signals": ["A"], "coverage_delta": {"A": "partial"},
            "candidate_quote": "q1", "grade": "thin", "reasoning": "r1",
            "active_question_id": "qid1"}},
        {"kind": "turn.decision", "payload": {
            "attributed_signals": [], "coverage_delta": {"B": "sufficient"},
            "candidate_quote": "q2", "grade": "concrete", "reasoning": "r2",
            "active_question_id": "qid2"}},
    ]}
    ev_a = collect_signal_evidence(env, "A")
    assert [t.candidate_quote for t in ev_a] == ["q1"]
    ev_b = collect_signal_evidence(env, "B")          # via coverage_delta key
    assert [t.candidate_quote for t in ev_b] == ["q2"]


def test_detect_knockout_close_uses_failing_turn_quote_not_filler():
    # The failing turn carries the incriminating quote; the close turn is a filler.
    env = {"events": [
        {"kind": "turn.decision", "payload": {
            "move": "advance", "attributed_signals": ["API"],
            "coverage_delta": {"API": "failed"},
            "candidate_quote": "I've never built any custom connectors."}},
        {"kind": "turn.decision", "payload": {
            "move": "knockout_close", "attributed_signals": [],
            "coverage_delta": {}, "candidate_quote": "Hello?"}},
    ]}
    ko = detect_knockout_close(env)
    assert ko is not None
    assert ko.signal == "API"
    assert ko.quote == "I've never built any custom connectors."   # NOT "Hello?"

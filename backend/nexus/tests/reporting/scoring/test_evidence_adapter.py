from __future__ import annotations

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.scoring.evidence_adapter import EvidenceView


def _evidence(**overrides) -> SessionEvidence:
    base = {
        "meta": {
            "session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
            "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
            "duration_s": 1200.0, "time_budget_s": 1200.0, "completion": "completed",
            "questions_asked": 2, "questions_core_total": 2, "questions_overflow_asked": 0,
        },
        "signals": [
            {"signal": "python", "signal_type": "competency", "weight": 3,
             "priority": "required", "knockout": True, "provenance": "asked_directly"},
            {"signal": "leadership", "signal_type": "behavioral", "weight": 1,
             "priority": "preferred", "knockout": False, "provenance": "cross_credited"},
            {"signal": "uncovered_role_sig", "signal_type": "competency", "weight": 2,
             "priority": "preferred", "knockout": False, "provenance": "not_reached"},
        ],
        "notes": [
            {"seq": 1, "turn_ref": "t-1", "signal": "python", "stance": "supports",
             "texture": "concrete", "quote": "I built X in Python", "span": {"start_ms": 0, "end_ms": 100},
             "from_question_id": "q1", "via_probe": False},
            {"seq": 2, "turn_ref": "t-2", "signal": "leadership", "stance": "supports",
             "texture": "strong", "quote": "I led a team of 5", "span": {"start_ms": 0, "end_ms": 100},
             "from_question_id": "q1", "via_probe": True},
        ],
        "questions": [
            {"question_id": "q1", "primary_signal": "python", "tier": "core",
             "outcome": "asked", "closure": "satisfied", "probes_used": [0], "probes_available": 3},
            {"question_id": "q2", "primary_signal": "communication", "tier": "core",
             "outcome": "not_reached", "closure": None, "probes_used": [], "probes_available": 2},
        ],
        "transcript": [
            {"turn_ref": "t-1", "speaker": "candidate", "text": "I built X in Python",
             "span": {"start_ms": 0, "end_ms": 100}, "pre_turn_gap_ms": 500},
            {"turn_ref": "t-0", "speaker": "agent", "text": "Tell me about Python",
             "span": {"start_ms": 0, "end_ms": 100}, "pre_turn_gap_ms": 0},
        ],
        "knockout": None,
    }
    base.update(overrides)
    return SessionEvidence.model_validate(base)


def test_primary_set_comes_from_question_primary_signals_not_signals_list():
    view = EvidenceView(_evidence())
    assert view.primary_set == {"python", "communication"}


def test_notes_by_signal_groups_supports():
    view = EvidenceView(_evidence())
    assert [n.quote for n in view.notes_by_signal["python"]] == ["I built X in Python"]


def test_demonstrated_secondaries_are_cross_credited_non_primary():
    view = EvidenceView(_evidence())
    assert view.demonstrated_secondaries == {"leadership"}


def test_candidate_transcript_text_excludes_agent_turns():
    view = EvidenceView(_evidence())
    assert view.candidate_transcript_text == "I built X in Python"


def test_knockout_close_detection():
    ev = _evidence(
        meta={**_evidence().meta.model_dump(mode="json"), "completion": "knockout_close"},
        knockout={"signal": "python", "or_alternatives_checked": [],
                  "reflect_confirmed": True, "evidence_note_seqs": [1]},
    )
    view = EvidenceView(ev)
    assert view.knockout_signal == "python"
    assert view.is_knockout_close is True

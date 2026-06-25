from types import SimpleNamespace
from app.modules.reporting.serialization import report_read_from_row


def _row(**kw):
    base = dict(
        id=None, session_id=None, status="ready", engine_version="v3", version=1,
        verdict="advance", verdict_reason="ok", overall_score=81, overall_coverage=0.8,
        overall_confidence="high",
        dimension_scores={
            "overall": {"score": 81, "tier_label": "Strong", "tone": "ok",
                        "confidence": "high", "coverage": 0.8,
                        "session_score": 80, "holistic_delta": 5},
            "technical": {"score": 83, "tier_label": "Strong", "tone": "ok",
                          "confidence": "high", "coverage": 0.8},
        },
        signal_scorecards=[{"signal": "Intune", "type": "competency", "weight": 3,
                            "knockout": False, "priority": "required",
                            "provenance": "asked_directly", "level": "strong",
                            "score": 100, "evidence": []}],
        question_scorecards=[], summary={}, scoring_manifest=None, human_decision=None,
        generated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_dimension_and_signal_scores_are_ten_scale():
    read = report_read_from_row(_row())
    assert read.overall_score == 8.1
    assert read.scores["overall"].score == 8.1
    assert read.scores["overall"].session_score == 8.0
    assert read.scores["overall"].holistic_delta == 0.5
    assert read.scores["technical"].score == 8.3
    assert read.signal_assessments[0].score == 10.0

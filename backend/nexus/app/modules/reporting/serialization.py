"""Shared SessionReport ORM row -> ReportRead mapping.

Used by the reporting router (HTTP read) and the share actor (PDF render) so
both produce an identical recruiter-facing report shape.
"""
from __future__ import annotations

from app.modules.reporting.models import SessionReport
from app.modules.reporting.schemas import ReportRead
from app.modules.reporting.scoring.scale import to_ten


def _convert_score_dict(d: dict) -> dict:
    """Convert score/session_score/holistic_delta keys from 0–100 to 0–10."""
    out = dict(d)
    for k in ("score", "session_score", "holistic_delta"):
        if k in out:
            out[k] = to_ten(out[k])
    return out


def report_read_from_row(row: SessionReport) -> ReportRead:
    """Assemble a ReportRead from a SessionReport ORM row.

    Column mapping (persist_report writes, this reads):
      dimension_scores    -> scores
      summary             -> decision, quick_summary, strengths, concerns, methodology
      question_scorecards -> questions
      signal_scorecards   -> signal_assessments

    Score conversion: internal 0–100 scores are converted to 0–10 (one decimal)
    at this read-model boundary. The DB and scoring engine remain 0–100.
    """
    summary = row.summary or {}
    raw_scores = row.dimension_scores or {}
    scores_ten = {k: _convert_score_dict(v) for k, v in raw_scores.items()}
    sig_cards = [dict(s, score=to_ten(s.get("score"))) for s in (row.signal_scorecards or [])]
    return ReportRead.model_validate(
        {
            "id": str(row.id) if row.id else None,
            "session_id": str(row.session_id) if row.session_id else None,
            "status": row.status,
            "engine_version": row.engine_version,
            "version": row.version,
            "verdict": row.verdict,
            "verdict_reason": row.verdict_reason,
            "overall_score": to_ten(row.overall_score),
            "overall_coverage": (
                float(row.overall_coverage) if row.overall_coverage is not None else 0.0
            ),
            "overall_confidence": row.overall_confidence or "low",
            "decision": summary.get("decision") or {
                "headline": row.verdict_reason or "",
                "why_positive": {"title": "", "body": ""},
                "why_negative": {"title": "", "body": ""},
            },
            "scores": scores_ten,
            "quick_summary": summary.get("quick_summary", ""),
            "strengths": summary.get("strengths", []),
            "concerns": summary.get("concerns", []),
            "questions": row.question_scorecards or [],
            "methodology": summary.get("methodology") or {"note": "", "charity_flags": []},
            "signal_assessments": sig_cards,
            "scoring_manifest": row.scoring_manifest,
            "human_decision": row.human_decision,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        }
    )

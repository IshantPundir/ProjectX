"""Shared SessionReport ORM row -> ReportRead mapping.

Used by the reporting router (HTTP read) and the share actor (PDF render) so
both produce an identical recruiter-facing report shape.
"""
from __future__ import annotations

from app.modules.reporting.models import SessionReport
from app.modules.reporting.schemas import ReportRead


def report_read_from_row(row: SessionReport) -> ReportRead:
    """Assemble a ReportRead from a SessionReport ORM row.

    Column mapping (persist_report writes, this reads):
      dimension_scores    -> scores
      summary             -> decision, quick_summary, strengths, concerns, methodology
      question_scorecards -> questions
      signal_scorecards   -> signal_assessments
    """
    summary = row.summary or {}
    return ReportRead.model_validate(
        {
            "id": str(row.id) if row.id else None,
            "session_id": str(row.session_id) if row.session_id else None,
            "status": row.status,
            "engine_version": row.engine_version,
            "version": row.version,
            "verdict": row.verdict,
            "verdict_reason": row.verdict_reason,
            "overall_score": row.overall_score,
            "overall_coverage": (
                float(row.overall_coverage) if row.overall_coverage is not None else 0.0
            ),
            "overall_confidence": row.overall_confidence or "low",
            "decision": summary.get("decision") or {
                "headline": row.verdict_reason or "",
                "why_positive": {"title": "", "body": ""},
                "why_negative": {"title": "", "body": ""},
            },
            "scores": row.dimension_scores or {},
            "quick_summary": summary.get("quick_summary", ""),
            "strengths": summary.get("strengths", []),
            "concerns": summary.get("concerns", []),
            "questions": row.question_scorecards or [],
            "methodology": summary.get("methodology") or {"note": "", "charity_flags": []},
            "signal_assessments": row.signal_scorecards or [],
            "scoring_manifest": row.scoring_manifest,
            "human_decision": row.human_decision,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        }
    )

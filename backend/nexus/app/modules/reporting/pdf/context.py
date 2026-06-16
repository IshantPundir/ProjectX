"""Pure helpers that turn a ReportRead into the print-template context.

No I/O, no Playwright — unit-testable anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.schemas import ReportRead

# Verdict (enum) -> stamp text + color. UI labels differ from enum values.
_STAMP = {
    "advance": ("APPROVED", "#138a47"),
    "borderline": ("BORDERLINE", "#c98a16"),
    "reject": ("REJECTED", "#d23b34"),
}

# Dimension key -> display name. "overall" is the gauge, not a meter.
_DIM_ORDER = [
    ("technical", "Technical"),
    ("behavioral", "Behavioral"),
    ("communication", "Communication"),
]


def _bar_color(score: int) -> str:
    if score >= 80:
        return "#137a45"
    if score >= 60:
        return "#b4791a"
    return "#d23b34"


@dataclass(frozen=True)
class StampSpec:
    text: str
    color: str


def verdict_stamp(verdict: str) -> StampSpec:
    text, color = _STAMP.get(verdict, ("PENDING", "#6b6f7a"))
    return StampSpec(text=text, color=color)


def monogram_initials(name: str | None) -> str:
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def assessed_dimensions(scores: dict) -> list[dict]:
    """Return [{name, score, color, tier}] for dimensions that were scored.

    A dimension with score=None (e.g. Behavioral on a technical-only screen) is
    omitted entirely — never shown as 0 or as a placeholder gauge.
    """
    out: list[dict] = []
    for key, label in _DIM_ORDER:
        dim = scores.get(key) or {}
        score = dim.get("score")
        if score is None:
            continue
        out.append({
            "name": label,
            "score": int(score),
            "color": _bar_color(int(score)),
            "tier": dim.get("tier_label") or "",
        })
    return out


def build_pdf_context(
    report: ReportRead,
    *,
    candidate_name: str,
    job_title: str,
    stage_label: str,
    generated_on: str,
    reference_photo_url: str | None,
    full_session_url: str,
) -> dict:
    """Flatten a ReportRead + session metadata into the print template context."""
    # report.scores values are ScoreOut models; coerce to dict for uniform access.
    scores_as_dict = {
        k: (v.model_dump() if hasattr(v, "model_dump") else dict(v))
        for k, v in report.scores.items()
    }
    overall_d = scores_as_dict.get("overall", {})
    return {
        "candidate_name": candidate_name,
        "monogram": monogram_initials(candidate_name),
        "job_title": job_title,
        "stage_label": stage_label,
        "generated_on": generated_on,
        "reference_photo_url": reference_photo_url,
        "full_session_url": full_session_url,
        "stamp": verdict_stamp(report.verdict),
        "overall_score": report.overall_score,
        "overall_tier": overall_d.get("tier_label") or "",
        "overall_color": (
            _bar_color(report.overall_score) if report.overall_score is not None else "#6b6f7a"
        ),
        "overall_confidence": report.overall_confidence,
        "overall_coverage_pct": round((report.overall_coverage or 0.0) * 100),
        "dimensions": assessed_dimensions(scores_as_dict),
        "decision": report.decision.model_dump(),
        "quick_summary": report.quick_summary,
        "strengths": [s.model_dump() for s in report.strengths],
        "concerns": [c.model_dump() for c in report.concerns],
        "questions": [q.model_dump() for q in report.questions],
    }

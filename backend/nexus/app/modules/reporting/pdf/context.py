"""Pure helpers that turn a ReportRead into the print-template context.

No I/O, no Playwright — unit-testable anywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

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

# Maximum radar axis count (keeps the spider chart readable).
_RADAR_MAX = 8


def _bar_color(score: float) -> str:
    if score >= 8.0:
        return "#137a45"
    if score >= 6.0:
        return "#b4791a"
    return "#d23b34"


def _star_fractions(score: int | float | None) -> list[float]:
    """Convert a 0–10 score to five half-star fill fractions in [0, 1].

    score / 2 gives the star count (0–5). Each fill value is:
      - 1.0 if the star is fully filled
      - 0.5 if the star is half-filled (fractional part >= 0.5)
      - 0.0 if the star is empty
    """
    if score is None:
        return [0.0] * 5
    stars_total = max(0.0, min(5.0, score / 2.0))
    fractions: list[float] = []
    for i in range(5):
        remaining = stars_total - i
        if remaining >= 1.0:
            fractions.append(1.0)
        elif remaining >= 0.5:
            fractions.append(0.5)
        else:
            fractions.append(0.0)
    return fractions


def _format_session_date(iso: str | None) -> str | None:
    """Parse ISO 8601 string → "Mon DD, YYYY" (e.g. "Jun 15, 2026")."""
    if not iso:
        return None
    try:
        # Handle both "Z" suffix and "+00:00" offset forms.
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %-d, %Y")
    except (ValueError, AttributeError):
        return iso  # passthrough on parse failure


def _format_duration(seconds: int | None) -> str | None:
    """Convert integer seconds → "MM:SS" string (e.g. 1845 → "30:45")."""
    if seconds is None:
        return None
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _build_header(report: ReportRead, *, candidate_name: str, job_title: str, stage_label: str) -> dict:
    """Build the header block from report.header when present; fall back to call-site params."""
    if report.header is not None:
        h = report.header
        return {
            "candidate_name": h.candidate_name,
            "candidate_email": h.candidate_email,
            "job_title": h.job_title or job_title,
            "stage_label": h.stage_label or stage_label,
            "session_date": _format_session_date(h.session_started_at),
            "duration": _format_duration(h.duration_seconds),
            "skills": list(h.skills),
        }
    # Legacy path — header not yet attached (older sessions or direct PDF calls).
    return {
        "candidate_name": candidate_name,
        "candidate_email": None,
        "job_title": job_title,
        "stage_label": stage_label,
        "session_date": None,
        "duration": None,
        "skills": [],
    }


def _build_radar(report: ReportRead) -> list[dict]:
    """Return up to _RADAR_MAX assessed primary-signal axis points for the radar chart.

    Filters:
    - provenance != "not_reached"  (signal was actually explored)
    - score is not None            (has a numeric 0–10 score)

    Sorted by weight desc, then signal name for deterministic tie-breaking.
    Capped at _RADAR_MAX entries.
    """
    assessed = [
        sa for sa in report.signal_assessments
        if sa.provenance != "not_reached" and sa.score is not None
    ]
    assessed.sort(key=lambda sa: (-sa.weight, sa.signal))
    return [
        {"name": sa.signal, "score": sa.score}
        for sa in assessed[:_RADAR_MAX]
    ]


def build_radar_geometry(radar: list[dict], *, size: float = 240) -> dict | None:
    """Precompute SVG coordinates for the radar spider chart.

    Args:
        radar: list of {"name": str, "score": float} — each score 0–10
        size: SVG canvas size (square), default 240

    Returns:
        None if len(radar) < 3 (template renders bar fallback instead).
        Otherwise a dict with:
          - size: SVG canvas size (passed through for template)
          - cx, cy: center point
          - R: max radius
          - grid_points: full-radius polygon points string
          - mid_points: 0.5-radius polygon points string
          - axes: list of {"x1": cx, "y1": cy, "x2": float, "y2": float} per axis
          - data_points: data polygon points string (each vertex at R * score/10)
          - labels: list of {"x": float, "y": float, "anchor": str, "name": str}
    """
    n = len(radar)
    if n < 3:
        return None

    cx = cy = size / 2.0
    # Leave room for labels around the edge.
    R = size / 2.0 - 30.0

    def _vertex(factor: float, i: int) -> tuple[float, float]:
        angle_deg = -90.0 + i * 360.0 / n
        rad = math.radians(angle_deg)
        return (cx + R * factor * math.cos(rad), cy + R * factor * math.sin(rad))

    def _pts(factor: float) -> str:
        return " ".join(f"{_vertex(factor, i)[0]:.2f},{_vertex(factor, i)[1]:.2f}" for i in range(n))

    # Data polygon: each vertex at R * (score / 10)
    data_pts_list: list[str] = []
    for i, ax in enumerate(radar):
        score = ax.get("score") or 0.0
        factor = max(0.0, min(1.0, score / 10.0))
        x, y = _vertex(factor, i)
        data_pts_list.append(f"{x:.2f},{y:.2f}")
    data_points = " ".join(data_pts_list)

    # Axes
    axes = []
    for i in range(n):
        gx, gy = _vertex(1.0, i)
        axes.append({"x1": cx, "y1": cy, "x2": gx, "y2": gy})

    # Labels — push 18px outward from grid edge
    label_factor = 1.0 + 18.0 / R
    labels = []
    for i, ax in enumerate(radar):
        lx, ly = _vertex(label_factor, i)
        if lx > cx + 2:
            anchor = "start"
        elif lx < cx - 2:
            anchor = "end"
        else:
            anchor = "middle"
        labels.append({"x": lx, "y": ly, "anchor": anchor, "name": ax["name"]})

    return {
        "size": size,
        "cx": cx,
        "cy": cy,
        "R": R,
        "grid_points": _pts(1.0),
        "mid_points": _pts(0.5),
        "axes": axes,
        "data_points": data_points,
        "labels": labels,
    }


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
            "score": score,
            "color": _bar_color(score),
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

    # Build questions list with score, full question_text, and star fractions.
    questions: list[dict] = []
    for q in report.questions:
        q_dict = q.model_dump()
        q_dict["stars"] = _star_fractions(q.score)
        questions.append(q_dict)

    # Derive the candidate_name and job_title for top-level legacy keys.
    header_block = _build_header(
        report,
        candidate_name=candidate_name,
        job_title=job_title,
        stage_label=stage_label,
    )
    # Top-level legacy keys use header data when available; otherwise fall back to params.
    effective_candidate_name = header_block["candidate_name"]
    effective_job_title = header_block["job_title"]
    effective_stage_label = header_block["stage_label"]

    # Build radar list once; reuse for both the raw list and precomputed geometry.
    radar_list = _build_radar(report)

    return {
        # ---- legacy top-level keys (template backwards-compat) ----
        "candidate_name": effective_candidate_name,
        "monogram": monogram_initials(effective_candidate_name),
        "job_title": effective_job_title,
        "stage_label": effective_stage_label,
        "generated_on": generated_on,
        "reference_photo_url": reference_photo_url,
        "full_session_url": full_session_url,
        # ---- verdict / score ----
        "stamp": verdict_stamp(report.verdict),
        "overall_score": report.overall_score,
        "overall_tier": overall_d.get("tier_label") or "",
        "overall_color": (
            _bar_color(report.overall_score) if report.overall_score is not None else "#6b6f7a"
        ),
        # ---- dimensions bar chart ----
        "dimensions": assessed_dimensions(scores_as_dict),
        # ---- prose ----
        "decision": report.decision.model_dump(),
        "quick_summary": report.quick_summary,
        "strengths": [s.model_dump() for s in report.strengths],
        "concerns": [c.model_dump() for c in report.concerns],
        # ---- per-question rows (score + question_text + stars) ----
        "questions": questions,
        # ---- C1/C2 additions ----
        "header": header_block,
        "radar": radar_list,
        "radar_geom": build_radar_geometry(radar_list),
    }

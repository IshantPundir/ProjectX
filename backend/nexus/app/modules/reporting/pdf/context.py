"""Pure helpers that turn a ReportRead into the print-template context.

No I/O, no Playwright — unit-testable anywhere.

Colors mirror the web report's "daylight" design tokens (frontend/app/app/theme.css)
so the shared PDF looks the same as the on-screen report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.modules.reporting.schemas import ReportRead


def _seal_path(cx: float = 50, cy: float = 50, spikes: int = 12,
               outer: float = 31, inner: float = 25) -> str:
    """12-point star path; rounded into a scalloped verified seal by a thick
    round stroke in the template (mirrors the web VerifiedBadge)."""
    step = math.pi / spikes
    rot = -math.pi / 2
    pts: list[str] = []
    for i in range(spikes * 2):
        r = outer if i % 2 == 0 else inner
        x = cx + math.cos(rot) * r
        y = cy + math.sin(rot) * r
        pts.append(f"{'M' if i == 0 else 'L'}{x:.2f},{y:.2f}")
        rot += step
    return "".join(pts) + "Z"


VERIFIED_SEAL_PATH = _seal_path()

# ── Verdict → rubber-stamp text + color (matches the web VerdictStamp) ─────────
_STAMP = {
    "advance": ("APPROVED", "#36d07f"),
    "borderline": ("BORDERLINE", "#f0b429"),
    "reject": ("REJECTED", "#ff6b6b"),
}

# ── Verdict → AI-recommendation label + ink color (web verdictMeta / TONE_INK) ─
_RECOMMENDATION = {
    "advance": ("Recommended", "#0B3D34"),
    "borderline": ("Borderline", "#4A3E7A"),
    "reject": ("Not Recommended", "#8A2733"),
}

# ── Verdict → photo glow + ring (web ImmersiveHeader VERDICT_GLOW) ─────────────
_GLOW = {
    "advance": ("rgba(54,208,127,0.60)", "rgba(54,208,127,0.55)"),
    "borderline": ("rgba(245,176,69,0.60)", "rgba(245,176,69,0.55)"),
    "reject": ("rgba(239,68,68,0.58)", "rgba(239,68,68,0.52)"),
}

# ── Score-band thresholds (single source of truth, mirrors web report-format) ──
REJECT_BAND = 4.0
ADVANCE_BAND = 6.5
REJECT_PCT = REJECT_BAND / 10 * 100      # 40
ADVANCE_PCT = ADVANCE_BAND / 10 * 100    # 65 (the hiring-bar marker)

# ── Tone fills (saturated) — web TONE_FILL, used for gauge rings + bar fills ───
_FILL_OK = "#AEE3D9"
_FILL_CAUTION = "#E8930C"
_FILL_DANGER = "#E5556B"
_FILL_NEUTRAL = "#E7EBEE"

# ── Tone inks — web TONE_INK, used for the ✓/⚠ glyphs ──────────────────────────
_INK_OK = "#0B3D34"
_INK_CAUTION = "#7A4A08"

# Dimension key -> display name. "overall" is rendered as its own gauge first.
_DIM_ORDER = [
    ("technical", "Technical"),
    ("behavioral", "Behavioral"),
    ("communication", "Communication"),
]


def gauge_color(score: float | int | None) -> str:
    """Tone-fill color for a 0–10 score, aligned to the web verdict bands."""
    if score is None:
        return _FILL_NEUTRAL
    if score >= ADVANCE_BAND:
        return _FILL_OK
    if score >= REJECT_BAND:
        return _FILL_CAUTION
    return _FILL_DANGER


def _star_fractions(score: int | float | None) -> list[float]:
    """Convert a 0–10 score to five half-star fill fractions in [0, 1]."""
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
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %-d, %Y")
    except (ValueError, AttributeError):
        return iso


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _build_header(report: ReportRead, *, candidate_name: str, job_title: str, stage_label: str) -> dict:
    """Header block from report.header when present; fall back to call-site params."""
    if report.header is not None:
        h = report.header
        return {
            "candidate_name": h.candidate_name,
            "candidate_email": h.candidate_email,
            "candidate_title": h.candidate_title,
            "candidate_location": h.candidate_location,
            "company_name": h.company_name,
            "job_title": h.job_title or job_title,
            "job_location": h.job_location,
            "work_arrangement": h.work_arrangement,
            "stage_label": h.stage_label or stage_label,
            "session_date": _format_session_date(h.session_started_at),
            "duration": _format_duration(h.duration_seconds),
            "skills": list(h.skills),
        }
    return {
        "candidate_name": candidate_name,
        "candidate_email": None,
        "candidate_title": None,
        "candidate_location": None,
        "company_name": None,
        "job_title": job_title,
        "job_location": None,
        "work_arrangement": None,
        "stage_label": stage_label,
        "session_date": None,
        "duration": None,
        "skills": [],
    }


def _build_bar(sa, *, must_have: bool) -> dict:
    """One threshold-banded competency bar (mirrors the web ScoreBar 'row')."""
    score = sa.score
    not_reached = sa.provenance == "not_reached"
    assessed = score is not None and not not_reached
    cleared = assessed and score >= ADVANCE_BAND
    fill_pct = max(0.0, min(100.0, (score / 10 * 100))) if assessed else 0.0
    hint = (getattr(sa, "level_basis", "") or "").strip() or None
    return {
        "label": sa.signal,
        "must_have": must_have,
        "assessed": assessed,
        "not_reached": not_reached,
        "cleared": cleared,
        "value": f"{score:.1f}" if assessed else None,
        "fill_pct": fill_pct,
        "fill_color": gauge_color(score) if assessed else _FILL_NEUTRAL,
        "glyph": ("✓" if cleared else "⚠") if assessed else None,
        "glyph_color": _INK_OK if cleared else _INK_CAUTION,
        "hint": hint,
    }


def _sort_key(sa):
    return (-sa.weight, sa.signal)


def build_competencies(report: ReportRead) -> dict:
    """Split signal assessments into must-have (knockout) vs other, web-style."""
    must = sorted((a for a in report.signal_assessments if a.knockout), key=_sort_key)
    other = sorted((a for a in report.signal_assessments if not a.knockout), key=_sort_key)
    return {
        "must_haves": [_build_bar(a, must_have=True) for a in must],
        "others": [_build_bar(a, must_have=False) for a in other],
    }


@dataclass(frozen=True)
class StampSpec:
    text: str
    color: str


def verdict_stamp(verdict: str) -> StampSpec:
    text, color = _STAMP.get(verdict, ("PENDING", "#6b6f7a"))
    return StampSpec(text=text, color=color)


def recommendation_meta(verdict: str) -> dict:
    label, ink = _RECOMMENDATION.get(verdict, ("Pending", "#5C6B73"))
    return {"label": label, "ink": ink}


def verdict_glow(verdict: str) -> dict:
    glow, ring = _GLOW.get(verdict, ("rgba(108,92,208,0.45)", "rgba(108,92,208,0.45)"))
    return {"glow": glow, "ring": ring}


def monogram_initials(name: str | None) -> str:
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def assessed_dimensions(scores: dict) -> list[dict]:
    """Return [{name, score, color}] for dimensions that were scored.

    A dimension with score=None is omitted entirely (never shown as 0).
    """
    out: list[dict] = []
    for key, label in _DIM_ORDER:
        dim = scores.get(key) or {}
        score = dim.get("score")
        if score is None:
            continue
        out.append({"name": label, "score": score, "color": gauge_color(score)})
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
    scores_as_dict = {
        k: (v.model_dump() if hasattr(v, "model_dump") else dict(v))
        for k, v in report.scores.items()
    }
    overall_d = scores_as_dict.get("overall", {})
    overall_score = report.overall_score

    questions: list[dict] = []
    for q in report.questions:
        q_dict = q.model_dump()
        q_dict["stars"] = _star_fractions(q.score)
        questions.append(q_dict)

    header_block = _build_header(
        report, candidate_name=candidate_name, job_title=job_title, stage_label=stage_label,
    )

    # Overall + dimension gauges, color-coded by value (web GlanceBand).
    gauges = [{"name": "Overall", "score": overall_score,
               "color": gauge_color(overall_score), "is_overall": True}]
    gauges += [{**d, "is_overall": False} for d in assessed_dimensions(scores_as_dict)]

    return {
        # ---- identity / chrome ----
        "candidate_name": header_block["candidate_name"],
        "monogram": monogram_initials(header_block["candidate_name"]),
        "job_title": header_block["job_title"],
        "stage_label": header_block["stage_label"],
        "generated_on": generated_on,
        "reference_photo_url": reference_photo_url,
        "full_session_url": full_session_url,
        "header": header_block,
        # ---- verdict ----
        "stamp": verdict_stamp(report.verdict),
        "recommendation": {**recommendation_meta(report.verdict),
                           "headline": report.decision.headline},
        "glow": verdict_glow(report.verdict),
        "verified_seal_path": VERIFIED_SEAL_PATH,
        # ---- gauges + competency bars (the glance band) ----
        "gauges": gauges,
        "overall_score": overall_score,
        "overall_tier": overall_d.get("tier_label") or "",
        "competencies": build_competencies(report),
        "bands": {"reject_pct": REJECT_PCT, "advance_pct": ADVANCE_PCT},
        # ---- prose sections ----
        "decision": report.decision.model_dump(),
        "quick_summary": report.quick_summary,
        "strengths": [s.model_dump() for s in report.strengths],
        "concerns": [c.model_dump() for c in report.concerns],
        "questions": questions,
    }

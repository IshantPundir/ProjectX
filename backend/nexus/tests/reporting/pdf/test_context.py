from app.modules.reporting.pdf.context import (
    monogram_initials,
    verdict_stamp,
    assessed_dimensions,
    derive_strengths,
    derive_watchouts,
)
from app.modules.reporting.schemas import SignalAssessmentOut


def test_verdict_stamp_mapping():
    assert verdict_stamp("advance").text == "APPROVED"
    assert verdict_stamp("advance").color == "#138a47"
    assert verdict_stamp("borderline").text == "BORDERLINE"
    assert verdict_stamp("borderline").color == "#c98a16"
    assert verdict_stamp("reject").text == "REJECTED"
    assert verdict_stamp("reject").color == "#d23b34"


def test_monogram_initials():
    assert monogram_initials("Ishant Pundir") == "IP"
    assert monogram_initials("madonna") == "M"
    assert monogram_initials("  Aarav Kumar Mehta ") == "AM"
    assert monogram_initials("") == "?"
    assert monogram_initials(None) == "?"


def test_assessed_dimensions_drops_unassessed():
    scores = {
        "overall": {"score": 90},
        "technical": {"score": 89, "tier_label": "Strong"},
        "behavioral": {"score": None, "coverage": 0.0},
        "communication": {"score": 70, "tier_label": "Strong"},
    }
    dims = assessed_dimensions(scores)
    names = [d["name"] for d in dims]
    assert names == ["Technical", "Communication"]  # overall + behavioral excluded
    assert dims[0]["score"] == 89
    assert dims[0]["tier"] == "Strong"               # tier carried for the gauge label


def test_assessed_dimensions_color_bands():
    scores = {
        "technical": {"score": 8.9},     # >=8.0 → green
        "communication": {"score": 7.0}, # 6.0..7.9 → amber
        "behavioral": {"score": 1.0},    # <6.0 → red
    }
    by_name = {d["name"]: d["color"] for d in assessed_dimensions(scores)}
    assert by_name["Technical"] == "#137a45"
    assert by_name["Communication"] == "#b4791a"
    assert by_name["Behavioral"] == "#d23b34"


from app.modules.reporting.pdf.context import build_pdf_context
from app.modules.reporting.schemas import ReportRead
from app.modules.reporting.pdf.render import build_pdf_html


def _min_report() -> ReportRead:
    return ReportRead.model_validate({
        "verdict": "advance", "verdict_reason": "ok",
        "overall_score": 90, "overall_coverage": 1.0, "overall_confidence": "high",
        "decision": {"headline": "h", "why_positive": {"title": "p", "body": "pb"},
                     "why_negative": {"title": "n", "body": "nb"}},
        "scores": {"overall": {"score": 90, "tier_label": "Strong", "tone": "ok",
                               "confidence": "high", "coverage": 1.0},
                   "technical": {"score": 89, "tier_label": "Strong", "tone": "ok",
                                 "confidence": "high", "coverage": 1.0},
                   "behavioral": {"score": None, "tier_label": "Not Assessed",
                                  "tone": "neutral", "confidence": "low", "coverage": 0.0},
                   "communication": {"score": 70, "tier_label": "Strong", "tone": "ok",
                                     "confidence": "medium", "coverage": 1.0}},
        "quick_summary": "Solid screen.",
        "strengths": [{"title": "s1", "detail": "d1"}],
        "concerns": [{"title": "c1", "detail": "d1", "severity": "moderate"}],
        "questions": [{"seq": 1, "question_id": "q1", "title": "Q one",
                       "status_badge": "passed", "status_tone": "ok",
                       "question_text": "text", "candidate_quote": "quote", "our_read": "",
                       "difficulty": "medium"}],
        "methodology": {"note": "", "charity_flags": []},
    })


def test_build_pdf_context_shape():
    ctx = build_pdf_context(
        _min_report(), candidate_name="Ishant Pundir", job_title="Engineer",
        stage_label="New Stage", generated_on="Jun 14, 2026",
        reference_photo_url=None, full_session_url="https://x/coming-soon",
    )
    assert ctx["candidate_name"] == "Ishant Pundir"
    assert ctx["monogram"] == "IP"
    assert ctx["stamp"].text == "APPROVED"
    assert ctx["overall_score"] == 90
    assert [d["name"] for d in ctx["dimensions"]] == ["Technical", "Communication"]
    assert ctx["reference_photo_url"] is None
    assert len(ctx["questions"]) == 1
    assert ctx["overall_color"] == "#137a45"   # 90 → green band, drives the gauge arc
    # Dead fields removed from the context (template never consumed them).
    assert "overall_confidence" not in ctx
    assert "overall_coverage_pct" not in ctx


# ---------------------------------------------------------------------------
# derive_strengths / derive_watchouts unit tests
# ---------------------------------------------------------------------------

def _sa(
    signal: str,
    level: str,
    weight: int,
    *,
    knockout: bool = False,
    priority: str = "medium",
) -> SignalAssessmentOut:
    return SignalAssessmentOut(
        signal=signal,
        type="skill",
        weight=weight,
        knockout=knockout,
        priority=priority,
        provenance="asked_directly",
        level=level,  # type: ignore[arg-type]
    )


class TestDeriveStrengths:
    def test_includes_solid_and_strong(self):
        assessments = [
            _sa("Alpha", "strong", weight=3),
            _sa("Beta", "solid", weight=2),
            _sa("Gamma", "thin", weight=1),
        ]
        result = derive_strengths(assessments)
        assert result == ["Alpha", "Beta"]

    def test_sorted_by_weight_desc(self):
        assessments = [
            _sa("Low", "solid", weight=1),
            _sa("High", "strong", weight=5),
            _sa("Mid", "solid", weight=3),
        ]
        result = derive_strengths(assessments)
        assert result == ["High", "Mid", "Low"]

    def test_cap_respected(self):
        assessments = [_sa(f"S{i}", "strong", weight=i) for i in range(10, 0, -1)]
        result = derive_strengths(assessments, cap=3)
        assert len(result) == 3
        assert result[0] == "S10"  # highest weight first

    def test_empty_assessments(self):
        assert derive_strengths([]) == []

    def test_no_solid_or_strong(self):
        assessments = [
            _sa("A", "thin", weight=5),
            _sa("B", "absent", weight=3),
            _sa("C", "not_reached", weight=2),
        ]
        assert derive_strengths(assessments) == []

    def test_thin_absent_not_included(self):
        assessments = [
            _sa("Solid One", "solid", weight=2),
            _sa("Thin One", "thin", weight=10),
        ]
        result = derive_strengths(assessments)
        assert result == ["Solid One"]
        assert "Thin One" not in result


class TestDeriveWatchouts:
    def test_knockout_with_negative_level_included(self):
        assessments = [
            _sa("Critical Skill", "thin", weight=5, knockout=True),
        ]
        result = derive_watchouts(assessments)
        assert result == ["Critical Skill"]

    def test_required_priority_with_negative_level_included(self):
        assessments = [
            _sa("Required Skill", "absent", weight=4, priority="required"),
        ]
        result = derive_watchouts(assessments)
        assert result == ["Required Skill"]

    def test_not_reached_required_is_watchout(self):
        assessments = [
            _sa("Untested Required", "not_reached", weight=3, priority="required"),
        ]
        result = derive_watchouts(assessments)
        assert result == ["Untested Required"]

    def test_solid_strong_required_not_a_watchout(self):
        """A required/knockout signal that was solid/strong is NOT a watch-out."""
        assessments = [
            _sa("Good Required", "solid", weight=5, knockout=True, priority="required"),
            _sa("Good KO", "strong", weight=4, knockout=True),
        ]
        result = derive_watchouts(assessments)
        assert result == []

    def test_thin_non_required_non_knockout_not_a_watchout(self):
        """A thin signal that is NOT required/knockout does not appear as a watch-out."""
        assessments = [
            _sa("Optional Thin", "thin", weight=5, knockout=False, priority="medium"),
        ]
        result = derive_watchouts(assessments)
        assert result == []

    def test_sorted_by_weight_desc_and_cap(self):
        assessments = [
            _sa("Low KO", "absent", weight=1, knockout=True),
            _sa("High KO", "thin", weight=10, knockout=True),
            _sa("Mid Req", "not_reached", weight=5, priority="required"),
            _sa("Extra", "absent", weight=3, knockout=True),
        ]
        result = derive_watchouts(assessments, cap=3)
        assert result == ["High KO", "Mid Req", "Extra"]

    def test_empty_assessments(self):
        assert derive_watchouts([]) == []


class TestPillsInHtml:
    def test_strength_pill_name_appears_in_rendered_html(self):
        """A derived strength signal name must appear in the rendered PDF HTML."""
        report_data = {
            "verdict": "advance", "verdict_reason": "ok",
            "overall_score": 8.5, "overall_coverage": 1.0, "overall_confidence": "high",
            "decision": {"headline": "h", "why_positive": {"title": "p", "body": "pb"},
                         "why_negative": {"title": "n", "body": "nb"}},
            "scores": {
                "overall": {"score": 8.5, "tier_label": "Strong", "tone": "ok",
                            "confidence": "high", "coverage": 1.0},
            },
            "quick_summary": "Strong candidate.",
            "strengths": [], "concerns": [],
            "questions": [],
            "methodology": {"note": "", "charity_flags": []},
            "signal_assessments": [
                {
                    "signal": "PythonExpertise", "score": 9.0, "weight": 5,
                    "provenance": "asked_directly", "level": "strong",
                    "type": "skill", "knockout": False, "priority": "required",
                },
                {
                    "signal": "MissingRequired", "score": None, "weight": 4,
                    "provenance": "not_reached", "level": "not_reached",
                    "type": "skill", "knockout": False, "priority": "required",
                },
            ],
        }
        report = ReportRead.model_validate(report_data)
        ctx = build_pdf_context(
            report,
            candidate_name="Test User",
            job_title="Engineer",
            stage_label="AI Screening",
            generated_on="Jun 19, 2026",
            reference_photo_url=None,
            full_session_url="https://x",
        )
        # strength pill: PythonExpertise (solid/strong)
        assert "PythonExpertise" in ctx["strengths_pills"]
        # watchout pill: MissingRequired (not_reached + required)
        assert "MissingRequired" in ctx["watchout_pills"]

        html = build_pdf_html(ctx)
        # Both names must appear as pills in the rendered HTML
        assert "PythonExpertise" in html
        assert "MissingRequired" in html
        # The label text for the strip must also be present
        assert "Top strengths" in html
        assert "Watch-outs" in html

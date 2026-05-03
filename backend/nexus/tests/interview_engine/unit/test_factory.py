"""Unit tests for tasks/factory.py — routing + per-task budget computation.

Coverage target: 100% branch on factory.py (load-bearing routing logic per
CLAUDE.md "candidate scoring and classification thresholds").
"""

from __future__ import annotations

from app.modules.interview_engine.tasks import (
    BehavioralStarTask,
    ComplianceBinaryTask,
    TechnicalDepthTask,
    build_task_for,
    effective_budget_seconds_for,
)
from app.modules.interview_engine.tasks.factory import _ROUTING_TABLE
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _make_question(*, kind: str = "technical_depth", est: float = 3.0) -> QuestionConfig:
    return QuestionConfig(
        id="q-fac-1",
        position=0,
        text="A long enough placeholder question text body goes here.",
        signal_values=["s1"],
        estimated_minutes=est,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["e1", "e2", "e3"],
        red_flags=["r1", "r2"],
        rubric=QuestionRubric(
            excellent="x" * 10, meets_bar="y" * 10, below_bar="z" * 10,
        ),
        evaluation_hint="evaluation hint at least 10 chars long",
        question_kind=kind,  # type: ignore[arg-type]
    )


class TestRoutingTable:
    def test_routing_table_has_all_four_kinds(self) -> None:
        assert set(_ROUTING_TABLE.keys()) == {
            "technical_depth", "behavioral_star",
            "compliance_binary", "open_culture",
        }

    def test_technical_depth_routes_to_technical_depth_task(self) -> None:
        q = _make_question(kind="technical_depth")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, TechnicalDepthTask)

    def test_behavioral_star_routes_to_behavioral_task(self) -> None:
        q = _make_question(kind="behavioral_star")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, BehavioralStarTask)

    def test_compliance_binary_routes_to_compliance_task(self) -> None:
        q = _make_question(kind="compliance_binary")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, ComplianceBinaryTask)

    def test_open_culture_falls_back_to_technical_depth_task(self) -> None:
        """open_culture is reserved but deferred — falls back per spec §1.2."""
        q = _make_question(kind="open_culture")
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, TechnicalDepthTask)

    def test_unknown_kind_falls_back_to_technical_depth_task(self) -> None:
        """Defensive — a future enum value should NOT crash; falls back to safe default.

        Constructs the QuestionConfig with model_construct to bypass the
        Literal validator (which would otherwise reject the unknown value).
        """
        q = QuestionConfig.model_construct(
            id="q-fac-2", position=0, text="x" * 60, signal_values=["s"],
            estimated_minutes=3.0, is_mandatory=True, follow_ups=[],
            positive_evidence=["e1", "e2", "e3"], red_flags=["r1", "r2"],
            rubric=QuestionRubric(excellent="x"*10, meets_bar="y"*10, below_bar="z"*10),
            evaluation_hint="ten chars yes", question_kind="not_a_real_kind",
        )
        t = build_task_for(q, controller=None, disqualified_signals=frozenset())
        assert isinstance(t, TechnicalDepthTask)


class TestEffectiveBudgetSecondsFor:
    def test_technical_depth_no_cap(self) -> None:
        q = _make_question(kind="technical_depth", est=3.0)
        # 3 * 60 = 180 + overhead. Overhead is settings-dependent; assert structure.
        secs = effective_budget_seconds_for(q)
        assert secs > 180.0  # at least the base
        assert secs < 200.0  # plus a small overhead, not a giant one

    def test_behavioral_no_cap(self) -> None:
        q = _make_question(kind="behavioral_star", est=4.0)
        secs = effective_budget_seconds_for(q)
        assert secs > 240.0
        assert secs < 260.0

    def test_compliance_capped_at_60(self) -> None:
        """estimated_minutes=2.0 → base ~125s; cap should bring it down to 60."""
        q = _make_question(kind="compliance_binary", est=2.0)
        secs = effective_budget_seconds_for(q)
        assert secs == 60.0

    def test_compliance_with_short_estimate_uses_base(self) -> None:
        """If estimated_minutes is small (~10s), base < cap, so base wins."""
        q = _make_question(kind="compliance_binary", est=0.1)
        secs = effective_budget_seconds_for(q)
        # 0.1 * 60 = 6s + overhead → still well under 60s cap
        assert secs < 60.0
        assert secs >= 6.0

    def test_open_culture_uses_technical_depth_budget_no_cap(self) -> None:
        q = _make_question(kind="open_culture", est=3.0)
        secs = effective_budget_seconds_for(q)
        assert secs > 180.0  # no cap (TechnicalDepthTask has none)

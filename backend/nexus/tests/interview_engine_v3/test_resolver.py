"""
Tests for brain/resolver.py — deterministic question resolver + time-budget.

Covers:
 1. on_track → next core by position (lowest unasked position)
 2. no-repeat: asked questions skipped
 3. mandatory-first when winding_down
 4. truncation → not_reached: winding_down, non-mandatory core, budget too low → None + not_reached record
 5. overflow by weight: no core left, uncovered overflow, higher-weight wins
 6. skip-if-covered (overflow): overflow question whose signal is already covered is skipped
 7. preference honored only with slack; ignored when winding_down
 8. full coverage → close (all asked → None)
 9. compute_budget_phase boundary checks
10. build_question_records: asked/not_reached + closure mapping
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.brain.resolver import (
    BudgetConfig,
    ResolverQuestion,
    build_question_records,
    compute_budget_phase,
    resolve_next,
)
from app.modules.interview_engine.contracts import BudgetPhase
from app.modules.interview_runtime.evidence import (
    QuestionOutcome,
    QuestionTier,
    ThreadClosure,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CFG = BudgetConfig(close_reserve_s=45.0, winding_down_s=90.0)

# Two core questions — q1 (position 1, non-mandatory), q2 (position 2, mandatory)
Q_CORE_1 = ResolverQuestion(
    question_id="q1",
    primary_signal="python",
    tier="core",
    is_mandatory=False,
    position=1,
    weight=2,
    estimated_minutes=5.0,
)
Q_CORE_2 = ResolverQuestion(
    question_id="q2",
    primary_signal="system_design",
    tier="core",
    is_mandatory=True,
    position=2,
    weight=3,
    estimated_minutes=5.0,
)
# Two overflow questions — different weights, different signals
Q_OVF_HIGH = ResolverQuestion(
    question_id="q3",
    primary_signal="kubernetes",
    tier="coverage",
    is_mandatory=False,
    position=3,
    weight=3,
    estimated_minutes=4.0,
)
Q_OVF_LOW = ResolverQuestion(
    question_id="q4",
    primary_signal="docker",
    tier="coverage",
    is_mandatory=False,
    position=4,
    weight=1,
    estimated_minutes=4.0,
)


# ---------------------------------------------------------------------------
# 1. on_track → next core by position (lowest unasked)
# ---------------------------------------------------------------------------

def test_on_track_returns_first_core_by_position():
    """On-track with both cores unasked → returns the lowest-position core (q1)."""
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is not None
    assert result.question_id == "q1"


# ---------------------------------------------------------------------------
# 2. no-repeat: asked questions are skipped
# ---------------------------------------------------------------------------

def test_no_repeat_skips_asked_question():
    """q1 already asked → resolver returns q2, not q1."""
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2],
        asked_ids={"q1"},
        covered_signals=set(),
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is not None
    assert result.question_id == "q2"


# ---------------------------------------------------------------------------
# 3. mandatory-first when winding_down
# ---------------------------------------------------------------------------

def test_winding_down_prefers_mandatory_over_lower_position():
    """
    Core has q1 (position 1, non-mandatory) and q2 (position 2, mandatory).
    Both unasked. winding_down → mandatory (q2) wins over lower position (q1).
    """
    # time_remaining_s is within winding_down_s range AND still above close_reserve + estimated
    # so q2 (mandatory) can be returned
    time_remaining_s = 80.0  # <= winding_down_s=90 → winding_down
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=time_remaining_s,
        cfg=CFG,
    )
    assert result is not None
    assert result.question_id == "q2"  # mandatory wins


# ---------------------------------------------------------------------------
# 4. truncation → not_reached: winding_down, non-mandatory core, budget too low
# ---------------------------------------------------------------------------

def test_truncation_returns_none_and_not_reached_record():
    """
    winding_down, only a non-mandatory core left with estimated_minutes=5 → needs
    time_remaining_s > close_reserve_s + 5*60 = 345.
    With time_remaining_s=80, non-mandatory core cannot fit → resolve_next returns None.
    build_question_records marks that question not_reached + closure=None.
    """
    non_mandatory_only = ResolverQuestion(
        question_id="qx",
        primary_signal="sql",
        tier="core",
        is_mandatory=False,
        position=1,
        weight=2,
        estimated_minutes=5.0,
    )
    time_remaining_s = 80.0  # winding_down; 80 <= 45 + 300 → can't fit

    result = resolve_next(
        questions=[non_mandatory_only],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=time_remaining_s,
        cfg=CFG,
    )
    assert result is None, "Non-mandatory core with insufficient budget must yield None (not_reached)"

    # build_question_records must mark it not_reached with closure=None
    records = build_question_records(
        questions=[non_mandatory_only],
        asked_ids=set(),
        closures={},
    )
    assert len(records) == 1
    rec = records[0]
    assert rec.question_id == "qx"
    assert rec.outcome == QuestionOutcome.not_reached
    assert rec.closure is None


# ---------------------------------------------------------------------------
# 5. overflow by weight: no core left, uncovered overflow → higher-weight wins
# ---------------------------------------------------------------------------

def test_overflow_higher_weight_wins():
    """
    All core asked. Two uncovered overflow questions: q3 (weight=3) and q4 (weight=1).
    Ample time → returns q3 (higher weight).
    """
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2, Q_OVF_HIGH, Q_OVF_LOW],
        asked_ids={"q1", "q2"},
        covered_signals=set(),
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is not None
    assert result.question_id == "q3"


# ---------------------------------------------------------------------------
# 6. skip-if-covered overflow: signal already covered → skip to uncovered lower-weight
# ---------------------------------------------------------------------------

def test_overflow_skips_covered_signal():
    """
    All core asked. q3 signal 'kubernetes' is already covered → resolver skips q3
    and returns q4 (lower weight but uncovered signal).
    """
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2, Q_OVF_HIGH, Q_OVF_LOW],
        asked_ids={"q1", "q2"},
        covered_signals={"kubernetes"},   # q3's signal is covered
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is not None
    assert result.question_id == "q4"


def test_overflow_all_covered_returns_none():
    """All core asked AND all overflow signals covered → None (close)."""
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2, Q_OVF_HIGH, Q_OVF_LOW],
        asked_ids={"q1", "q2"},
        covered_signals={"kubernetes", "docker"},
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 7. preference honored with slack; ignored when winding_down
# ---------------------------------------------------------------------------

def test_preference_honored_on_track_with_budget():
    """
    on_track + preferred_next_signal matches an unasked question with budget →
    that question is returned even if lower position would normally win.
    """
    # q1 (pos 1) would normally be next, but preferred_next_signal = "system_design" (q2)
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=600.0,
        cfg=CFG,
        preferred_next_signal="system_design",  # matches q2
    )
    assert result is not None
    assert result.question_id == "q2"


def test_preference_ignored_when_winding_down():
    """
    winding_down + preferred_next_signal → preference is IGNORED.
    Mandatory core (q2) wins instead.
    """
    time_remaining_s = 80.0  # winding_down; q2 is mandatory and fits (80 > 45 + 5*60? No!)
    # With estimated_minutes=5 for q2, need: time_remaining_s > 45 + 300 = 345
    # So with 80s, even mandatory q2 doesn't FIT the budget check... but the spec says:
    # "mandatory is NEVER dropped by the budget" — the budget check applies only to non-mandatory.
    # Re-read: "if winding: prefer mandatory → mandatory-first returns it UNCONDITIONALLY (never dropped)"
    # The budget check only applies to the non-mandatory fallback path.
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=time_remaining_s,
        cfg=CFG,
        preferred_next_signal="python",  # matches q1 (non-mandatory)
    )
    assert result is not None
    assert result.question_id == "q2"  # mandatory overrides the preference


def test_preference_ignored_when_insufficient_budget():
    """
    on_track but preferred question would not fit in budget →
    preference is ignored, normal core ordering resumes.
    """
    # budget: time_remaining_s must be > close_reserve_s + pref.estimated_minutes*60
    # q2 estimated_minutes=5 → 45 + 300 = 345 needed
    # We set time_remaining_s = 300 (on_track, but not enough for q2 pref)
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=300.0,  # on_track (> 90), but < 345
        cfg=CFG,
        preferred_next_signal="system_design",  # q2 doesn't fit budget
    )
    # Falls through to normal core-first: q1 (pos 1) is returned
    assert result is not None
    assert result.question_id == "q1"


# ---------------------------------------------------------------------------
# 8. full coverage → close (all questions asked → None)
# ---------------------------------------------------------------------------

def test_full_coverage_returns_none():
    """All questions asked → resolve_next returns None (close)."""
    result = resolve_next(
        questions=[Q_CORE_1, Q_CORE_2, Q_OVF_HIGH, Q_OVF_LOW],
        asked_ids={"q1", "q2", "q3", "q4"},
        covered_signals=set(),
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is None


def test_empty_bank_returns_none():
    """Empty question bank → None immediately."""
    result = resolve_next(
        questions=[],
        asked_ids=set(),
        covered_signals=set(),
        time_remaining_s=600.0,
        cfg=CFG,
    )
    assert result is None


# ---------------------------------------------------------------------------
# 9. compute_budget_phase boundary checks
# ---------------------------------------------------------------------------

def test_compute_budget_phase_on_track():
    """time_remaining_s > winding_down_s → on_track."""
    assert compute_budget_phase(91.0, CFG) == BudgetPhase.on_track


def test_compute_budget_phase_exactly_at_threshold():
    """time_remaining_s == winding_down_s → winding_down."""
    assert compute_budget_phase(90.0, CFG) == BudgetPhase.winding_down


def test_compute_budget_phase_below_threshold():
    """time_remaining_s < winding_down_s → winding_down."""
    assert compute_budget_phase(50.0, CFG) == BudgetPhase.winding_down


def test_compute_budget_phase_zero():
    """time_remaining_s = 0 → winding_down."""
    assert compute_budget_phase(0.0, CFG) == BudgetPhase.winding_down


def test_compute_budget_phase_two_values_only():
    """BudgetPhase has exactly two values: on_track and winding_down."""
    values = set(BudgetPhase)
    assert values == {BudgetPhase.on_track, BudgetPhase.winding_down}


# ---------------------------------------------------------------------------
# 10. build_question_records: asked→outcome=asked+closure; not_asked→not_reached+closure None
# ---------------------------------------------------------------------------

def test_build_question_records_full():
    """
    q1 asked with closure=satisfied, q2 asked with closure=tapped_out,
    q3 not asked → not_reached + closure None.
    """
    questions = [Q_CORE_1, Q_CORE_2, Q_OVF_HIGH]
    asked_ids = {"q1", "q2"}
    closures = {
        "q1": ThreadClosure.satisfied,
        "q2": ThreadClosure.tapped_out,
    }

    records = build_question_records(
        questions=questions,
        asked_ids=asked_ids,
        closures=closures,
        asked_at_turn={"q1": "turn-001", "q2": "turn-003"},
        probes_used={"q1": [0], "q2": []},
        probes_available={"q1": 3, "q2": 2, "q3": 1},
        time_spent_s={"q1": 120.0, "q2": 90.0},
    )

    by_id = {r.question_id: r for r in records}

    # q1: asked, satisfied
    assert by_id["q1"].outcome == QuestionOutcome.asked
    assert by_id["q1"].closure == ThreadClosure.satisfied
    assert by_id["q1"].tier == QuestionTier.core
    assert by_id["q1"].asked_at_turn == "turn-001"
    assert by_id["q1"].probes_used == [0]
    assert by_id["q1"].probes_available == 3
    assert by_id["q1"].time_spent_s == 120.0

    # q2: asked, tapped_out
    assert by_id["q2"].outcome == QuestionOutcome.asked
    assert by_id["q2"].closure == ThreadClosure.tapped_out
    assert by_id["q2"].tier == QuestionTier.core
    assert by_id["q2"].probes_used == []

    # q3: not reached → closure must be None
    assert by_id["q3"].outcome == QuestionOutcome.not_reached
    assert by_id["q3"].closure is None
    assert by_id["q3"].tier == QuestionTier.coverage


def test_build_question_records_not_reached_closure_none():
    """Un-asked question gets outcome=not_reached and closure=None, regardless of closures map."""
    questions = [Q_CORE_1]
    records = build_question_records(
        questions=questions,
        asked_ids=set(),
        closures={"q1": ThreadClosure.satisfied},  # would be ignored since not asked
    )
    assert len(records) == 1
    assert records[0].outcome == QuestionOutcome.not_reached
    assert records[0].closure is None


def test_build_question_records_defaults():
    """build_question_records handles absent optional maps with correct defaults."""
    records = build_question_records(
        questions=[Q_CORE_1],
        asked_ids={"q1"},
        closures={"q1": ThreadClosure.absent},
    )
    assert len(records) == 1
    rec = records[0]
    assert rec.outcome == QuestionOutcome.asked
    assert rec.closure == ThreadClosure.absent
    assert rec.asked_at_turn is None
    assert rec.probes_used == []
    assert rec.probes_available == 0
    assert rec.time_spent_s == 0.0


def test_build_question_records_tier_mapping():
    """Tier is correctly mapped from string to QuestionTier enum."""
    records = build_question_records(
        questions=[Q_OVF_HIGH],
        asked_ids=set(),
        closures={},
    )
    assert records[0].tier == QuestionTier.coverage


# ---------------------------------------------------------------------------
# 11. budget_config_from_ai_config round-trip
# ---------------------------------------------------------------------------

def test_budget_config_from_ai_config():
    """budget_config_from_ai_config reads from AIConfig defaults correctly."""
    from app.modules.interview_engine.brain.resolver import budget_config_from_ai_config

    cfg = budget_config_from_ai_config()
    assert isinstance(cfg, BudgetConfig)
    # The defaults set in Settings must match the documented F3-tuned defaults
    assert cfg.close_reserve_s == 45.0
    assert cfg.winding_down_s == 90.0

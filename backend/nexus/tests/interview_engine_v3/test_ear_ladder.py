"""Tests for the Ear's pure fusion ladder (B1).

Table-driven: every row covers one logical case from the §4 fusion rule.
No I/O, no LiveKit, no database — pure unit tests.
"""

from __future__ import annotations

import pytest

from app.modules.interview_engine.ear.ladder import (
    EarDecision,
    EarLadderConfig,
    decide,
)

# ---------------------------------------------------------------------------
# Shared config with KNOWN thresholds — all table rows reference this.
# Values are chosen to give unambiguous integer test inputs.
# ---------------------------------------------------------------------------
CFG = EarLadderConfig(
    smart_turn_commit_thr=0.5,   # voice_complete if smart_turn_prob >= 0.5
    text_commit_thr=0.02,        # text_complete if text_eou_prob >= 0.02
    min_silence_ms=300,          # below this → WAIT unconditionally
    hold_cue_ms=2500,            # at or above this (& incomplete) → HOLD_CUE
)

# Shorthand aliases for cleaner table cells
COMMIT = EarDecision.commit
WAIT = EarDecision.wait
HOLD = EarDecision.hold_cue

# Silence value used for the main quadrant tests: well past min_silence_ms
# (300) but below hold_cue_ms (2500) → only the voice/text signals matter.
MID_SILENCE = 1000


# ---------------------------------------------------------------------------
# Parametrized table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label, vad_ms, smart_prob, text_prob, expected", [
    # ------------------------------------------------------------------
    # 4 quadrants at MID_SILENCE — well above min, below hold_cue
    # ------------------------------------------------------------------
    # (complete, complete) → COMMIT  — §4 both-complete case
    (
        "Q1_complete_complete",
        MID_SILENCE, 0.9, 0.5,
        COMMIT,
    ),
    # (complete, incomplete) → COMMIT  — gen-2 rescue: voice overrides text
    (
        "Q2_complete_incomplete",
        MID_SILENCE, 0.9, 0.005,
        COMMIT,
    ),
    # (incomplete, complete) → WAIT  — never cut off mid-word
    (
        "Q3_incomplete_complete",
        MID_SILENCE, 0.1, 0.5,
        WAIT,
    ),
    # (incomplete, incomplete) → WAIT  — both unsure, silence not long enough
    (
        "Q4_incomplete_incomplete",
        MID_SILENCE, 0.1, 0.005,
        WAIT,
    ),

    # ------------------------------------------------------------------
    # Explicit disagreement cases (redundant with Q2/Q3 but named clearly)
    # ------------------------------------------------------------------
    # voice-finished + text-unsure → COMMIT  (gen-2 rescue)
    (
        "disagreement_voice_done_text_unsure",
        MID_SILENCE, 0.7, 0.0,
        COMMIT,
    ),
    # text-complete + voice-going → WAIT  (protect mid-word)
    (
        "disagreement_text_done_voice_going",
        MID_SILENCE, 0.0, 0.1,
        WAIT,
    ),

    # ------------------------------------------------------------------
    # hold_cue timing — both incomplete
    # ------------------------------------------------------------------
    # Long silence (>= hold_cue_ms) + both incomplete → HOLD_CUE
    (
        "hold_cue_long_silence",
        2500, 0.1, 0.005,
        HOLD,
    ),
    # Just below hold_cue_ms + both incomplete → WAIT
    (
        "hold_cue_below_threshold",
        2499, 0.1, 0.005,
        WAIT,
    ),
    # Long silence + both incomplete, with text_eou_prob present
    (
        "hold_cue_very_long_silence",
        5000, 0.1, 0.005,
        HOLD,
    ),

    # ------------------------------------------------------------------
    # min_silence_ms floor — WAIT regardless of signals
    # ------------------------------------------------------------------
    # Both complete but silence too short → WAIT
    (
        "floor_both_complete_short_silence",
        299, 0.9, 0.5,
        WAIT,
    ),
    # Zero silence
    (
        "floor_zero_silence",
        0, 0.99, 0.99,
        WAIT,
    ),
    # Exactly at min_silence_ms boundary — allowed through
    (
        "floor_exactly_min_silence_voice_complete",
        300, 0.9, 0.5,
        COMMIT,
    ),

    # ------------------------------------------------------------------
    # Smart-Turn-only fallback (text_eou_prob=None)
    # ------------------------------------------------------------------
    # voice complete → COMMIT
    (
        "st_only_voice_complete",
        MID_SILENCE, 0.9, None,
        COMMIT,
    ),
    # voice incomplete + long silence → HOLD_CUE  (mid-thought pause)
    (
        "st_only_long_silence_voice_incomplete",
        2500, 0.1, None,
        HOLD,
    ),
    # voice incomplete + short silence → WAIT
    (
        "st_only_short_silence_voice_incomplete",
        MID_SILENCE, 0.1, None,
        WAIT,
    ),
    # voice complete + short silence → WAIT (floor applies first)
    (
        "st_only_short_silence_voice_complete_floor",
        100, 0.9, None,
        WAIT,
    ),
    # voice complete exactly at min_silence_ms boundary
    (
        "st_only_voice_complete_exactly_at_floor",
        300, 0.7, None,
        COMMIT,
    ),
])
def test_decide(
    label: str,
    vad_ms: int,
    smart_prob: float,
    text_prob: float | None,
    expected: EarDecision,
) -> None:
    """Each row maps inputs + config → expected EarDecision."""
    result = decide(
        vad_silence_ms=vad_ms,
        smart_turn_prob=smart_prob,
        text_eou_prob=text_prob,
        cfg=CFG,
    )
    assert result == expected, (
        f"[{label}] decide(vad={vad_ms}, smart={smart_prob}, text={text_prob}) "
        f"= {result!r}, want {expected!r}"
    )


# ---------------------------------------------------------------------------
# Boundary values on the threshold itself
# ---------------------------------------------------------------------------

def test_smart_turn_commit_thr_boundary_exactly_at() -> None:
    """smart_turn_prob == smart_turn_commit_thr counts as complete."""
    result = decide(
        vad_silence_ms=MID_SILENCE,
        smart_turn_prob=CFG.smart_turn_commit_thr,  # exactly 0.5
        text_eou_prob=None,
        cfg=CFG,
    )
    assert result == COMMIT


def test_smart_turn_commit_thr_boundary_just_below() -> None:
    """smart_turn_prob just below thr counts as incomplete."""
    result = decide(
        vad_silence_ms=MID_SILENCE,
        smart_turn_prob=CFG.smart_turn_commit_thr - 0.001,  # 0.499
        text_eou_prob=None,
        cfg=CFG,
    )
    assert result == WAIT


def test_text_commit_thr_boundary_exactly_at() -> None:
    """text_eou_prob == text_commit_thr counts as complete."""
    result = decide(
        vad_silence_ms=MID_SILENCE,
        smart_turn_prob=0.1,           # voice incomplete
        text_eou_prob=CFG.text_commit_thr,  # exactly 0.02 → text complete
        cfg=CFG,
    )
    # voice incomplete, text complete → WAIT (never cut off mid-word)
    assert result == WAIT


def test_text_commit_thr_boundary_just_below() -> None:
    """text_eou_prob just below thr counts as incomplete → WAIT (below hold_cue)."""
    result = decide(
        vad_silence_ms=MID_SILENCE,
        smart_turn_prob=0.1,                      # voice incomplete
        text_eou_prob=CFG.text_commit_thr - 0.001,  # 0.019 → text incomplete
        cfg=CFG,
    )
    assert result == WAIT


def test_hold_cue_boundary_exactly_at_with_text() -> None:
    """vad_silence_ms == hold_cue_ms + both incomplete → HOLD_CUE."""
    result = decide(
        vad_silence_ms=CFG.hold_cue_ms,  # exactly 2500
        smart_turn_prob=0.1,
        text_eou_prob=0.005,
        cfg=CFG,
    )
    assert result == HOLD


def test_hold_cue_boundary_exactly_at_no_text() -> None:
    """vad_silence_ms == hold_cue_ms + voice incomplete + no text → HOLD_CUE."""
    result = decide(
        vad_silence_ms=CFG.hold_cue_ms,
        smart_turn_prob=0.1,
        text_eou_prob=None,
        cfg=CFG,
    )
    assert result == HOLD

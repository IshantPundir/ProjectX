"""
Gen-3 Interview Engine — deterministic question resolver + time-budget.

This module owns EXCLUSIVELY:
  • Which question is asked next (mandatory-coverage guarantee, no-repeat, no LLM leapfrogging)
  • Whether the session should close (budget exhausted / all questions asked)
  • Session-end `QuestionRecord` finalization for the engine→report contract

The brain NEVER hard-selects the next main question. It may emit an OPTIONAL
`preferred_next_signal` hint that this resolver honors ONLY when:
  (a) the session is on_track (not winding_down), AND
  (b) the preferred question fits within the time budget.
Otherwise mandatory-first ordering is enforced unconditionally.

Design: PURE logic — no livekit, no I/O, no LLM, no async.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.interview_engine.contracts import BudgetPhase
from app.modules.interview_runtime.evidence import (
    QuestionOutcome,
    QuestionRecord,
    QuestionTier,
    ThreadClosure,
)

if TYPE_CHECKING:
    pass


# ============================================================================
# Config
# ============================================================================

@dataclass(frozen=True)
class BudgetConfig:
    """Time-budget parameters for the resolver.

    [VALIDATE] Defaults are F3-tuned reasonable starting points; tune on talk-tests.
    """
    close_reserve_s: float   # seconds held back for the closing sequence
    winding_down_s: float    # time_remaining_s ≤ this → winding_down phase


# ============================================================================
# ResolverQuestion — compact bank view (no rubric, no text)
# ============================================================================

@dataclass(frozen=True)
class ResolverQuestion:
    """The resolver's compact view of one bank question.

    Intentionally tiny — no rubric text, no follow-up probes. Everything the
    resolver needs to make a scheduling decision and nothing more.
    """
    question_id: str
    primary_signal: str
    tier: str              # "core" | "coverage"
    is_mandatory: bool
    position: int          # absolute ordering within the bank (ascending = earlier)
    weight: int            # weight of the primary_signal (1–3); used to rank overflow
    estimated_minutes: float   # expected thread duration in minutes (for budget checks)


# ============================================================================
# Budget phase
# ============================================================================

def compute_budget_phase(time_remaining_s: float, cfg: BudgetConfig) -> BudgetPhase:
    """Two-valued budget signal — the ONLY time signal the brain sees.

    Returns `winding_down` when `time_remaining_s` has fallen to or below
    `cfg.winding_down_s`; `on_track` otherwise.
    """
    if time_remaining_s <= cfg.winding_down_s:
        return BudgetPhase.winding_down
    return BudgetPhase.on_track


# ============================================================================
# Main resolver
# ============================================================================

def resolve_next(
    *,
    questions: list[ResolverQuestion],
    asked_ids: set[str],
    covered_signals: set[str],
    time_remaining_s: float,
    cfg: BudgetConfig,
    preferred_next_signal: str | None = None,
) -> ResolverQuestion | None:
    """Return the next question to ask, or None to CLOSE the session.

    Logic (applied in order — the first matching branch wins):

    1. Unasked filter — if every question has been asked, return None (full coverage, close).
    2. Core partition — split unasked into core (sorted by position) and overflow.
    3. Budget phase — compute winding/on_track once.
    4. Preference (honored only with slack + coverage-safe):
       If a preferred_next_signal is provided AND we are on_track AND the
       matching unasked question fits within (time_remaining_s - close_reserve_s),
       return that question immediately.
    5. Core first (mandatory never dropped):
       a. winding_down:
          - If any mandatory core remains, return the first one (by position).
            Mandatory questions are NEVER subject to the budget check — they must
            be asked regardless.
          - Otherwise check whether the first non-mandatory core fits the budget.
            If yes, return it; if no (not_reached), fall through to close.
       b. on_track: return core_unasked[0] (lowest position, no budget check).
    6. Overflow by weight (uncovered signal only):
       Sort overflow candidates by (-weight, position). If the best one fits the
       budget, return it; otherwise close.
    7. None — close.
    """

    # ── 1. Build unasked list ────────────────────────────────────────────────
    unasked: list[ResolverQuestion] = [
        q for q in questions if q.question_id not in asked_ids
    ]
    if not unasked:
        # Full coverage — every question has been asked; time to close.
        return None

    # ── 2. Core partition (sorted by position ascending) ────────────────────
    core_unasked: list[ResolverQuestion] = sorted(
        [q for q in unasked if q.tier == "core"],
        key=lambda q: q.position,
    )

    # ── 3. Budget phase ──────────────────────────────────────────────────────
    winding: bool = compute_budget_phase(time_remaining_s, cfg) == BudgetPhase.winding_down

    # ── 4. Preference (honored ONLY with slack AND coverage-safe) ────────────
    # "Coverage-safe" means: we are NOT winding down (when winding down, focus
    # mandatory — never chase a preference). Budget-safe means the question fits
    # within the remaining window minus the close reserve.
    if preferred_next_signal is not None and not winding:
        # Find the first unasked question matching the preferred signal.
        pref: ResolverQuestion | None = next(
            (q for q in unasked if q.primary_signal == preferred_next_signal),
            None,
        )
        if pref is not None:
            # Honor only when there is enough budget for this question.
            if time_remaining_s > cfg.close_reserve_s + pref.estimated_minutes * 60:
                return pref
            # Budget too tight for the preference → fall through to normal ordering.

    # ── 5. Core first (mandatory NEVER dropped by the budget) ───────────────
    if core_unasked:
        if winding:
            # 5a — winding_down: mandatory-first
            mandatory_core: list[ResolverQuestion] = [
                q for q in core_unasked if q.is_mandatory
            ]
            if mandatory_core:
                # Mandatory core questions are returned unconditionally — the budget
                # check does NOT apply. A mandatory question MUST be asked even if
                # the clock is tight; failing to ask it would silently invalidate
                # the session's mandatory-coverage guarantee.
                return mandatory_core[0]

            # No mandatory core remains. Consider the next non-mandatory core only
            # if it fits within the budget.
            next_core: ResolverQuestion = core_unasked[0]
            if time_remaining_s > cfg.close_reserve_s + next_core.estimated_minutes * 60:
                return next_core

            # Non-mandatory core doesn't fit → this question becomes `not_reached`.
            # Fall through to overflow check (which will also fail the budget) → close.

        else:
            # 5b — on_track: return the next core by position (no budget check).
            return core_unasked[0]

    # ── 6. Overflow by weight (uncovered signal only) ────────────────────────
    # Only questions whose primary_signal is NOT yet in covered_signals are eligible.
    # Rank: highest weight first, then lowest position as tiebreaker.
    overflow: list[ResolverQuestion] = sorted(
        [
            q for q in unasked
            if q.tier == "coverage" and q.primary_signal not in covered_signals
        ],
        key=lambda q: (-q.weight, q.position),
    )
    if overflow and time_remaining_s > cfg.close_reserve_s + overflow[0].estimated_minutes * 60:
        return overflow[0]

    # ── 7. Nothing fits → close ──────────────────────────────────────────────
    return None


# ============================================================================
# Session-end record builder
# ============================================================================

def build_question_records(
    *,
    questions: list[ResolverQuestion],
    asked_ids: set[str],
    closures: dict[str, ThreadClosure],
    asked_at_turn: dict[str, str] | None = None,
    probes_used: dict[str, list[int]] | None = None,
    probes_available: dict[str, int] | None = None,
    time_spent_s: dict[str, float] | None = None,
) -> list[QuestionRecord]:
    """Build one `QuestionRecord` per bank question for the engine→report contract.

    Called once at session end. The loop supplies the per-thread `closures` it
    inferred from the brain's final coverage_after + stance + whether the
    time-resolver force-closed the thread.

    Rules:
      - outcome = `asked`       when question_id in asked_ids
      - outcome = `not_reached` otherwise
      - closure = closures.get(question_id) ONLY for asked questions;
                  not_reached questions always have closure=None (no thread ran)
    """
    _asked_at_turn = asked_at_turn or {}
    _probes_used = probes_used or {}
    _probes_available = probes_available or {}
    _time_spent_s = time_spent_s or {}

    records: list[QuestionRecord] = []
    for q in questions:
        qid = q.question_id
        if qid in asked_ids:
            # Thread was opened — record what happened.
            records.append(QuestionRecord(
                question_id=qid,
                primary_signal=q.primary_signal,
                tier=QuestionTier(q.tier),
                outcome=QuestionOutcome.asked,
                closure=closures.get(qid),  # None only if loop forgot to set it
                asked_at_turn=_asked_at_turn.get(qid),
                probes_used=_probes_used.get(qid, []),
                probes_available=_probes_available.get(qid, 0),
                time_spent_s=_time_spent_s.get(qid, 0.0),
            ))
        else:
            # Never reached — no thread, no closure.
            records.append(QuestionRecord(
                question_id=qid,
                primary_signal=q.primary_signal,
                tier=QuestionTier(q.tier),
                outcome=QuestionOutcome.not_reached,
                closure=None,  # no thread ran → closure is meaningless
                asked_at_turn=None,
                probes_used=[],
                probes_available=_probes_available.get(qid, 0),
                time_spent_s=0.0,
            ))
    return records


# ============================================================================
# Config factory
# ============================================================================

def budget_config_from_ai_config() -> BudgetConfig:
    """Build a BudgetConfig from the module-level AIConfig singleton.

    This is the production path. Tests may construct BudgetConfig directly.
    """
    from app.ai.config import ai_config  # local import — keeps resolver livekit-free at module level
    return BudgetConfig(
        close_reserve_s=ai_config.engine_close_reserve_s,
        winding_down_s=ai_config.engine_winding_down_s,
    )
